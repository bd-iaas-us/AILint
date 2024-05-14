
import os
import sys
import requests
import json
import re
from git import Repo
from fastapi.responses import JSONResponse, Response, StreamingResponse
from urllib.parse import urlparse
from task import Task, TaskStatus
from datetime import datetime
import time
import git
import threading

swe_dir = '/root/git/SWE-agent'
sys.path.append(swe_dir)

import run

from logger import init_logger
logger = init_logger(__name__)

# All the project related files, include repository, files generated by agent
# are saved in a folder generated by owner&repo under WORK_SPACE
import tempfile

WORK_SPACE = tempfile.gettempdir() + "/ailint/workspace"


# Dict for all the tasks 
from threading import Thread, Lock

# Python built-in dict is multi-thread safe for single operation
# But not for operations like: dict[i] = dick[j], dict[i] += 1 etc
# Add a mutex to guard tasks for peaceful mind
tasks_mutex = Lock()
tasks = {}

# The token used to access github
github_token = os.environ.get('GITHUB_TOKEN')

class SWEAgent:
    def __init__(self, data_path, repo_path):
        '''
        data_path: the path to the issue file, should be end with .md or .txt
        repo_path: the path to the repo directory, should be clean except the issue file
        '''
        self.data_path = data_path
        self.repo_path = repo_path

    def run(self):
        '''
        traj_dir has following structure:
            patches: contain diff files, .patch
            .jsonl: model_stats
            args.yaml: run arguments
            .traj: execution environment, trajectory and history
        '''
        script_args = run.get_args(['--model_name', 'gpt4', '--data_path', self.data_path, '--repo_path', self.repo_path, '--config_file', 'config/default_from_url.yaml', '--apply_patch_locally'])
        run_main = run.Main(script_args)
        threading.Thread(target=run_main.main).start()
        return os.path.join(swe_dir, run_main.traj_dir)

# The function generate a suffix based upon the datetime it is called
def gen_folder_suffix():
    return datetime.now().strftime("%m-%d-%Y_%H-%M-%S")

# The function get the path where the repository is cloned
# the path is created if not exists
def get_repo_folder(owner: str, repo: str, suffix=""):
    # Specify the path where you want to create the folder
    folder_path = WORK_SPACE + f'/{owner}/{repo}'
    if len(suffix) > 0:
        folder_path = folder_path + "_" + suffix

    if not os.path.exists(folder_path):
        # Create the folder
        os.makedirs(folder_path, exist_ok=True)
        return False, folder_path
    return True, folder_path

# The function get the path where the data is saved
# the path is created if not exists
def get_data_folder(owner: str, repo: str, suffix=""):
    # Specify the path where you want to create the folder
    folder_path = WORK_SPACE + f'/{owner}/{repo}_data'
    if len(suffix) > 0:
        folder_path = folder_path + "_" + suffix

    if not os.path.exists(folder_path):
        # Create the folder
        os.makedirs(folder_path, exist_ok=True)
        return False, folder_path
    return True, folder_path

# The function retrieve the owner and repository parts from url object
def get_owner_repo(url_obj):
    path = url_obj.path
    tokens = path.split("/")
    i = 0
    if len(tokens[i]) == 0:
        i += 1
    return tokens[i], tokens[i+1]


# The function generate the github API url, like
#   https://api.github.com/repos/{owner}/{repo}
#  NOTE: The function is not used for now, but keep it
#  in case we call github api in the future
def gen_github_api_url(url_obj):
    github_api = url_obj.hostname
    # If the host name starts with "www.", trim it
    if github_api.startswith("www."):
       github_api = github_api[4:]
    # Assemble the github api url
    github_api = "api." + github_api[4:]
    owner, repo = get_owner_repo(url_obj)
    return f'{url_obj.scheme}://{github_api}/repos/{owner}/{repo}/'

# Parses the promptObj and return the url object
def parse_prompt_obj(prompt_obj: dict):
    repo_url = prompt_obj["repo"]
    if repo_url is not None:
        url_obj = urlparse(repo_url)
        return url_obj
    return None


# "tasks" is a built-in dict object and thread-safe for single
# operations like tasks[taskId], but not for the task in "tasks"
# Here we don't use a global lock to guard "tasks",  
def get_task(taskId):
    with tasks_mutex:
        if taskId in tasks:
            return tasks[taskId]
    return None

def add_task(task: Task):
    with tasks_mutex:
        # task id is a UUID, dup not checked here
        tasks[task.id] = task

# The handler function retrieve the task
def handle_task(taskId):
    logger.info(f'getting task info for taskId: {taskId}')

    task = get_task(taskId)
    if task is None:
        message = f'The task {taskId} does not exist'
        return JSONResponse(status_code=404, content={'message': message})
    # get patch
    patch_file = task.get_patch_file()
    patch = ""
    if patch_file is not None:
        logger.info(f'reading patch file: {patch_file}')
        with open(patch_file, 'r') as f:
            patch = f.read()

    return JSONResponse(status_code=200, content={'status': task.get_status().name, 'patch': patch})

# Only support "https:" scheme for the moment
def clone_repo(url_obj: str, folder_suffix: str):
    owner, repo = get_owner_repo(url_obj)
    repo_exist, repo_folder = get_repo_folder(owner, repo, folder_suffix)
    if not repo_exist:
        url = f'{url_obj.scheme}://{url_obj.hostname}/{owner}/{repo}.git'
        git.Git(repo_folder).clone(url)
        return True, repo_folder
    return False, repo_folder

# The handler function for repo prompt
def handle_prompt(prompt_obj: dict):
    """
    The worker function to handle a dev request for a  project based
    prompt
    The promptObj contains "repo", "token" and "prompt"
    """
    prompt = prompt_obj["prompt"]
    if "repo" not in prompt_obj or prompt is None:
        return JSONResponse(status_code=400, content={'message': "The prompt & repo must be present in request"})

    logger.info(f'processing prompt: {prompt}')

    url_obj = parse_prompt_obj(prompt_obj)
    if url_obj is None or url_obj.path is None:
        return JSONResponse(status_code=400, content={'message': "The repo specified is invalid"})

    new_task = Task(prompt_obj["prompt"])
    add_task(new_task)

    folder_suffix = gen_folder_suffix()
    success, repo_folder = clone_repo(url_obj, folder_suffix)
    if not success:
        return JSONResponse(status_code=400, content={'message': "The repo specified exists or cannot be cloned"})

    prompt_file_name = f'{repo_folder}/prompt_{new_task.get_id()}.txt'
    with open(prompt_file_name, "w") as prompt_file:
        prompt_file.write(prompt)
 
    new_task.set_status(TaskStatus.RUNNING)
    # Call agent to query

    agent = SWEAgent(prompt_file_name, repo_folder)
    result_dir = agent.run()
    new_task.set_data_dir(result_dir)

    return JSONResponse(status_code=200, content={"task_id": new_task.get_id()})


def gen_history_data(taskId: str, url: str):
    data = {}
    task = get_task(taskId)
    if task is None:
        return json.dumps(data)

    # Reset the list history index, also the last modified time for time out purpose
    task.set_last_history_idx(-1)
    empty = ""
    logger.info(f'----------- start processing the history for task {task.get_id()} at {datetime.now()}')
    while True:
        if task.timed_out():
            logger.info(f'----------- Timed out at: {datetime.now()}, task: {task.get_id()}')
            return json.dumps(empty)

        hist_file_name = task.get_history_file()
        # The file with history data has not been created yet
        if hist_file_name is None:
            time.sleep(1)
            continue

        # Load the history data
        parsed_json = None
        with open(hist_file_name) as hist_file:
            # Here we directly read the file into memory
            file_content = hist_file.read()
        if file_content is None:
            time.sleep(1)
            continue

        try: 
            parsed_json = json.loads(file_content)
        except Exception as e:
            logger.warning(f'file is not in json format, {e}')
            time.sleep(1)

        # If the file is always empty, check time out
        if parsed_json is None:
            time.sleep(1)
            continue

        # Send new history data back
        index = -1
        last_index = task.get_last_history_idx()
        for item in parsed_json["history"]:
            if item["role"] != "assistant":
                continue

            if last_index != -1 and index < last_index:
                index += 1
                continue

            index += 1
            task.set_last_history_idx(index)

            data["role"] = item["role"]
            data["thought"] = item["thought"]
            data["content"] = item["content"]
            yield json.dumps(data, indent = 4)

            # If the action contain "submit", finished
            action = item["action"]
            if action is not None and "submit" in action:
                logger.info(f'----------- Submit found, finished processing the history for task {task.get_id()} at {datetime.now()}')
                task.set_status(TaskStatus.DONE)
                return json.dumps(empty)
    task.set_status(TaskStatus.Done)

def feed_history(task):
    data = {}
    for i in range(10):
        data["role"] = "assistant"
        data["thought"] = f'fake thought {i}'
        data["content"] = f'fake content {i}'
        if i == 9:
            data["action"] = 'submit'
        else:
            data["action"] = "fake action"
        logger.info(f'generate history data {i} at: {datetime.now()}')
        task.append_history(json.dumps(data, indent = 4))
        time.sleep(1)

def start_feed(taskId):
    task = get_task(taskId)
    task.clear_history()
    thread = Thread(target = feed_history, args = (task, ))
    thread.daemon = True
    thread.start()

# NOTE: The function can not be called with gen_history_data at the same time
def gen_history_data_v2(taskId: str, url: str):
    # Testing code, keep it here temporarily
    #start_feed(taskId)

    data = {}
    task = get_task(taskId)
    if task is None:
        return json.dumps(data)

    # Should be merged to task after the "/dev/histories" retires
    last_idx = -1
    last_modified = datetime.now()
    empty = ""
    logger.info(f'----------- v2: start processing the history for task {task.get_id()} at {datetime.now()}')
    while True:
        if task.timed_out2(datetime.now(), last_modified):
            logger.info(f'-----------v2: Timed out at: {datetime.now()}, task: {task.get_id()}')
            return json.dumps(empty)

        for i in range(last_idx + 1, task.get_history_len()):
            try: 
                item = json.loads(task.get_history(i))
                if item is None or item["role"] != "assistant":
                    continue
                last_idx += 1
                last_modified = datetime.now()

                data["role"] = item["role"]
                data["thought"] = item["thought"]
                data["content"] = item["content"]
                logger.info(f'----yield data at {datetime.now()}')
                yield json.dumps(data, indent = 4)

                # If the action contain "submit", finished
                action = item["action"]
                if action is not None and "submit" in action:
                    logger.info(f'----------- Submit found, finished processing the history for task {task.get_id()} at {datetime.now()}')
                    task.set_status(TaskStatus.DONE)
                    return json.dumps(empty)

            except Exception as e:
                logger.warning(f'file is not in json format, {e}')
                time.sleep(1)

