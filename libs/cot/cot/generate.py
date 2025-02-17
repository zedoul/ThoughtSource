import datetime
import json
import os
import pkgutil
import time
import uuid
from dataclasses import asdict

import datasets as ds
from cot.config import Config

# disable transformation (e.g. map) caching
# https://huggingface.co/docs/datasets/v2.6.1/en/package_reference/main_classes#datasets.disable_caching
ds.disable_caching()

FRAGMENTS = json.loads(pkgutil.get_data(__name__, "fragments.json"))


def generate_and_extract(data, config):
    """
    It takes a dataset and a config and generates cots for each example and extract answers.

    :param data: Dataset/DatasetDict - the dataset you want to generate CoTs for and extract answers
    :param config: Dictionary - the configurations of the input and model
    :return: the dataset with generated cots and extracted answers
    """

    ds.disable_caching()

    if isinstance(data, ds.arrow_dataset.Dataset):
        features = data.info.features
        if "idx_range" in config and config["idx_range"] != "all":
            n_samples = config["idx_range"][1] - config["idx_range"][0]
        else:
            n_samples = len(data)
    elif isinstance(data, ds.dataset_dict.DatasetDict):
        features = data["train"].info.features
        if "idx_range" in config and config["idx_range"] != "all":
            n_samples = (config["idx_range"][1] - config["idx_range"][0]) * len(data)
        else:
            n_samples = sum([len(data[x]) for x in data])
    else:
        raise ValueError("Not recognized data")

    if ("warn" not in config or config["warn"]) and not config["api_service"]=="mock_api":
        print_warning(config, n_samples)

    # The config is transformed into a dataclass object, where all testing is done
    # But it will be transformed back to a dictionary for the function 'map'
    config_as_dataclass = Config(**config)

    return data.map(
        _generate_and_extract,
        with_indices=True,
        fn_kwargs=asdict(config_as_dataclass),
        features=features,
    )


def _generate_and_extract(
    item,
    idx,
    # all of the following variables will be defined by the config_as_dataclass object
    idx_range,
    author,
    api_service,
    engine,
    temperature,
    max_tokens,
    api_time_interval,
    instruction_keys,
    cot_trigger_keys,
    template_cot_generation,
    answer_extraction_keys,
    template_answer_extraction,
    warn,
    verbose,
):

    """
    The function takes in a JSON object (item) and generates a CoT (Chain-of-Thought) for each combination of
    of instructions and CoT triggers. For each generated CoT and for each of the given answer extractions it extracts an answer.

    :param item: the item (example) of a dataset to be processed
    :param idx: the index of the item in the dataset
    other parameters are handed over from config and are described in config.py

    :return: item populated with various fields
    """

    if idx_range == "all" or (idx >= idx_range[0] and idx < idx_range[1]):
        pass
    else:
        return item

    # predefine values in template dictionary that stay same over all runs of the current item
    template_dict = {
        "instruction": None,
        "question": item["question"],
        "answer_choices": multiple_choice_answer_formatting(item["choices"]),
        "cot_trigger": None,
        "cot": None,
        "answer_extraction": None,
    }

    # generate chain of thoughts and extract answers
    for instruction_key in instruction_keys:
        template_dict["instruction"] = get_fragments_value(
            "instructions", instruction_key
        )

        for cot_trigger_key in cot_trigger_keys:
            generated_cot = {
                "id": str(uuid.uuid4()),
                "fragments_version": FRAGMENTS["version"],
                "instruction": instruction_key,
                "cot_trigger": cot_trigger_key,
                "cot_trigger_template": template_cot_generation,
                "prompt_text": "",
                "cot": "",
                "answers": [],
                "author": author,
                "date": "",
                "api_service": api_service,
                "model": str(
                    {
                        "name": engine,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    }
                ),
                "comment": "",
                "annotation": [],
            }

            template_dict["cot_trigger"] = get_fragments_value(
                "cot_triggers", cot_trigger_key
            )

            # change template_cot_generation to generated_cot["cot_trigger_template"] to make it more logical
            generate_cot_prompt = format_prompt(template_cot_generation, template_dict)

            if verbose:
                print("\n-----------------COT TRIGGER TEXT-----------------")
                print(generate_cot_prompt)

            cot = query_model(
                generate_cot_prompt,
                api_service,
                engine,
                temperature,
                max_tokens,
                api_time_interval,
            )
            if verbose:
                print("\n------------------GENERATED COT-------------------")
                print(cot)

            template_dict["cot"] = cot

            generated_cot["cot"] = cot

            # deactivated automatic prompt text generation: (code line stays here for testing purposes)
            # generated_cot["prompt_text"] = generate_cot_prompt

            generated_cot["date"] = print_now(1)

            # extract answers from generated chain of thoughts
            for answer_extraction_key in answer_extraction_keys:

                if answer_extraction_key is None:
                    pass

                else:
                    answer = {
                        "id": str(uuid.uuid4()),
                        "answer_extraction": answer_extraction_key,
                        "answer_extraction_template": template_answer_extraction,
                        "answer_extraction_text": "",
                        "answer": "",
                        "correct_answer": None,
                    }

                    template_dict["answer_extraction"] = get_fragments_value(
                        "answer_extractions", answer_extraction_key
                    )
                    answer_extraction_prompt = format_prompt(
                        template_answer_extraction, template_dict
                    )

                    if verbose:
                        print(
                            "\n----------------ANSWER EXTRACTION TEXT----------------"
                        )
                        print(answer_extraction_prompt)

                    predicted_answer = query_model(
                        answer_extraction_prompt,
                        api_service,
                        engine,
                        temperature,
                        max_tokens,
                        api_time_interval,
                    )
                    if verbose:
                        print("\n------------------EXTRACTED ANSWER-------------------")
                        print(predicted_answer)

                    answer["answer"] = predicted_answer

                    # deactivated automatic prompt text generation: (code line stays here for testing purposes)
                    # answer["answer_extraction_text"] = answer_extraction_prompt

                    generated_cot["answers"].append(answer)

            item["generated_cot"].append(generated_cot)

    return item

def full_text_prompts(dataset, prompt_text=True, answer_extraction_text = True):

    assert isinstance(
        dataset, ds.arrow_dataset.Dataset
    ), "dataset must be an arrow dataset"
    
    dataset = dataset.map(
    _full_text_prompts,
    fn_kwargs={"prompt_text": prompt_text, "answer_extraction_text": answer_extraction_text},
    features=dataset.info.features,
    )

    return dataset

def _full_text_prompts(item, prompt_text, answer_extraction_text):
    # predefine values in template dictionary that stay same over all runs of the current item
    template_dict = {
        "instruction": None,
        "question": item["question"],
        "cot_trigger": None,
        "cot": None,
        "answer_extraction": None,
    }

    for generated_cot in item["generated_cot"]:              
        answer_choices = multiple_choice_answer_formatting(item["choices"]),
        
        # function returns a tuple instead of a string
        # did not find out why it behaves differently here than in the _generate_and_extract function
        if type(answer_choices) == tuple:
            answer_choices = answer_choices[0]

        template_dict["answer_choices"] = answer_choices

        # generate chain of thoughts and extract answers
        # for instruction_key in instruction_keys:
        template_dict["instruction"] = get_fragments_value(
            "instructions", generated_cot["instruction"]
        )

        template_dict["cot_trigger"] = get_fragments_value(
            "cot_triggers", generated_cot["cot_trigger"]
        )
        
        generate_cot_prompt = format_prompt(generated_cot["cot_trigger_template"], template_dict)

        template_dict["cot"] = generated_cot["cot"]
        # Everything above could also be relevant for the answer extraction
        
        # now generating the full text for the chain of thoughts
        if prompt_text:
            generated_cot["prompt_text"] = generate_cot_prompt

        # if answer_extraction: ...
        if answer_extraction_text:
            # extract answers from generated chain of thoughts
            for answer in generated_cot["answers"]:

                if answer["answer_extraction"] is None:
                    # if no answer extraction key is given, return item, since cot_prompt text is already generated
                    return item

                else:

                    template_dict["answer_extraction"] = get_fragments_value(
                        "answer_extractions", answer["answer_extraction"]
                    )
                    answer_extraction_prompt = format_prompt(
                        answer["answer_extraction_template"], template_dict
                    )

                    answer["answer_extraction_text"] = answer_extraction_prompt

    return item


def print_now(return_flag=0):
    """
    It takes a flag as an argument and prints the current time in a specific format

    :param return_flag: 0 = print, 1 = return, defaults to 0 (optional)
    :return: the current time in the format of 'YYYY/MM/DD HH:MM:SS'
    """
    now = datetime.datetime.now()
    now = now.strftime("%Y/%m/%d %H:%M:%S")
    if return_flag == 0:
        print(now)
    elif return_flag == 1:
        return now
    else:
        pass


def print_warning(config, n_samples):
    n_instruction_keys = len(config["instruction_keys"]) if "instruction_keys" in config else 1
    n_cot_trigger_keys = len(config["cot_trigger_keys"]) if "cot_trigger_keys" in config else 1
    n_answer_extraction_keys = len(config["answer_extraction_keys"]) if "answer_extraction_keys" in config else 1

    n_total = (
        n_samples * n_instruction_keys * n_cot_trigger_keys
        + n_samples * n_instruction_keys * n_cot_trigger_keys * n_answer_extraction_keys
    )
    warning = f"""
        You are about to \033[1m call an external API \033[0m in total {n_total} times, which \033[1m may produce costs \033[0m.
        API calls for reasoning chain generation: {n_samples} samples  * {n_instruction_keys} instructions  * {n_cot_trigger_keys} reasoning chain triggers
        API calls for answer extraction: n_samples  {n_samples} samples  * {n_instruction_keys} instructions  * {n_cot_trigger_keys} reasoning chain triggers * {n_answer_extraction_keys} answer extraction triggers 
        Do you want to continue? y/n
        """
    if config["api_service"] == "mock_api":
        warning += "\033[1m Note: You are using a mock api. When entering 'y', a test run without API calls is made. \033[0m"
    print(warning)
    time.sleep(1)
    ans = input()
    if ans.lower() == "y":
        pass
    else:
        # break the execution of the code if the user does not want to continue
        raise ValueError("Generation aborted by user.")

def multiple_choice_answer_formatting(answer_choices):
    '''Transforms a list of answer choices into a string with letters (A,B,C,...) for each answer choice.'''
    # only supports uppercase letters at the moment, as this is current standard 

    # Adding Letters (A,B,C,...) for the given multiple choice answers.
    return "\n".join(
        [
            f"{chr(65+i)}) {example}" for i, example in enumerate(answer_choices)
        ]  # 65 is the ASCII code for A
    )


def get_fragments_value(str, key):
    if key is None:
        return None
    else:
        return FRAGMENTS[str][key]


def format_prompt(template, dictionary):
    output = template.format_map(Correct_output(dictionary))
    # remove leading whitespaces
    output = output.lstrip()
    return output


class Correct_output(dict):
    # TODO: do I ever need this? I think there will never be missing keys
    # and None keys are handled by delete_empty_curly_brackets
    def __missing__(self, key):
        return ""

    def __getitem__(self, key):
        return dict.get(self, key) or ""

    # def get(self, key):
    #     return dict.get(self, key) or ""


# def delete_empty_curly_brackets(string):
#     string.replace("{None}\n", "")
#     # string.replace("\n{None}", "") # TODO: do I need this?
#     string.replace("{None}", "")
#     return string


def query_model(input, api_service, engine, temperature, max_tokens, api_time_interval):
    if api_service == "mock_api":
        return " Test mock chain of thought."
        # return ("This is a " + 20 * "long " + "Mock CoT.\n")*20

    # langchain package implementation
    else:
        from langchain import LLMChain, Prompt

        time.sleep(api_time_interval)
        template = "{prompt}"
        prompt = Prompt(template=template, input_variables=["prompt"])

        if api_service == "openai":
            from langchain import OpenAI

            llm_chain = LLMChain(
                prompt=prompt,
                llm=OpenAI(
                    # parameter options: https://beta.openai.com/docs/api-reference/completions/create-completion
                    model_name=engine,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    # type: ignore (suppress pylance error)
                ),
            )
        if api_service == "huggingface_hub":
            from langchain import HuggingFaceHub

            llm_chain = LLMChain(
                prompt=prompt,
                llm=HuggingFaceHub(
                    # parameter options: https://huggingface.co/docs/api-inference/detailed_parameters
                    repo_id=engine,
                    model_kwargs={"temperature": temperature, "max_length": max_tokens},
                    # type: ignore (suppress pylance error)
                ),
            )
        response = llm_chain.predict(prompt=input, stop=None)
        return response
