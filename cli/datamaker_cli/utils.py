#  Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License").
#    You may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.

import logging
import math
import os
import random
import time
from typing import Any, Callable, Dict, Iterable, List, Optional

import boto3
import botocore.exceptions

_logger: logging.Logger = logging.getLogger(__name__)


def chunkify(lst: List[Any], num_chunks: int = 1, max_length: Optional[int] = None) -> List[List[Any]]:
    num: int = num_chunks if max_length is None else int(math.ceil((float(len(lst)) / float(max_length))))
    return [lst[i : i + num] for i in range(0, len(lst), num)]  # noqa: E203


def get_region() -> str:
    session = boto3.Session()
    if session.region_name is None:
        raise ValueError(
            "It is not possible to infer AWS REGION from your environment. Please pass the --region argument."
        )
    return str(session.region_name)


def get_account_id() -> str:
    return str(boto3.client(service_name="sts").get_caller_identity().get("Account"))


def namedtuple_to_dict(obj: Any) -> Any:
    if hasattr(obj, "_asdict"):  # NamedTuple
        return dict(zip(obj._fields, (namedtuple_to_dict(item) for item in obj)))
    elif isinstance(obj, str):
        return obj
    elif isinstance(obj, dict):
        return dict(zip(obj.keys(), (namedtuple_to_dict(item) for item in obj.values())))
    elif isinstance(obj, Iterable):
        return list((namedtuple_to_dict(item) for item in obj))
    return obj


def path_from_filename(filename: str) -> str:
    return os.path.dirname(os.path.realpath(filename))


def upsert_subnet_tag(subnet_id: str, key: str, value: str) -> None:
    ec2: Any = boto3.resource("ec2")
    ec2.Subnet(subnet_id).create_tags(Tags=[{"Key": key, "Value": value}])


def replace_dashes(original: Dict[str, Any]) -> Dict[str, Any]:
    return {k.replace("-", "_"): v for k, v in original.items()}


def replace_underscores(original: Dict[str, Any]) -> Dict[str, Any]:
    aux = {k.replace("_", "-"): v for k, v in original.items()}
    ret = {}
    for k, v in aux.items():
        if k.startswith("-"):
            ret[k[1:]] = v
        else:
            ret[k] = v
    return ret


def extract_plugin_name(func: Callable[..., None]) -> str:
    name = func.__module__.split(sep=".", maxsplit=1)[0]
    return name


def extract_images_names(env_name: str) -> List[str]:
    resp_type = Dict[str, List[Dict[str, List[Dict[str, str]]]]]
    try:
        response: resp_type = boto3.client("cloudformation").describe_stacks(StackName=f"datamaker-{env_name}")
    except botocore.exceptions.ClientError as ex:
        error: Dict[str, Any] = ex.response["Error"]
        if (
            error["Code"] == "ValidationError"
            and f"Stack with id datamaker-{env_name} does not exist" in error["Message"]
        ):
            return []
        raise
    if len(response["Stacks"]) < 1:
        return []
    if "Outputs" not in response["Stacks"][0]:
        return []
    for output in response["Stacks"][0]["Outputs"]:
        if output["ExportName"] == f"datamaker-{env_name}-repos":
            _logger.debug("Export value: %s", output["OutputValue"])
            return output["OutputValue"].split(",")
    raise RuntimeError(f"Stack datamaker-{env_name} does not have the expected datamaker-{env_name}-repos output.")


def try_it(f: Callable[..., Any], ex: Any, base: float = 1.0, max_num_tries: int = 3, **kwargs: Any) -> Any:
    """Run function with decorrelated Jitter.

    Reference: https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/
    """
    delay: float = base
    for i in range(max_num_tries):
        try:
            return f(**kwargs)
        except ex as exception:
            if i == (max_num_tries - 1):
                raise exception
            delay = random.uniform(base, delay * 3)
            _logger.error("Retrying %s | Fail number %s/%s | Exception: %s", f, i + 1, max_num_tries, exception)
            time.sleep(delay)
