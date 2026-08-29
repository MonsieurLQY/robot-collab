# RoCo: Dialectic Multi-Robot Collaboration with Large Language Models
Codebase for paper: RoCo: Dialectic Multi-Robot Collaboration with Large Language Models

[Mandi Zhao](https://mandizhao.github.io), [Shreeya Jain](https://www.linkedin.com), [Shuran Song](https://www.cs.columbia.edu/~shurans/) 

[Arxiv](https://arxiv.org/abs/2307.04738) | [Project Website](https://project-roco.github.io) 

 
<img src="method.jpeg" alt="method" width="800"/>


## Setup
### setup conda env and package install
```
conda create -n roco python=3.9
conda activate roco
```
### Install mujoco and dm_control 
```
pip install mujoco==2.3.0
pip install dm_control==1.0.8 
```
**If you have M1 Macbook like me and would like to visualize the task scenes locally:**

Download the macos-compatible `.dmg` file from [MuJoCo release page](https://github.com/deepmind/mujoco/releases), inside it should have a `MuJoCo.app` file that you can drag into your /Application folder, so it becomes just like other apps in your Mac. You could then open up the app and drag xml files in it. Find more informationa in the [official documentation](https://mujoco.readthedocs.io/en/latest/programming/#getting-started).

### Install other packages
```
pip install -r requirements.txt
pip install open3d==0.14.1
pip install -e .
```

### Configure Vertex AI Gemini
The default model is `gemini-3.7-flash` through Vertex AI. Authentication uses
a Google Cloud service-account JSON file. By default the simulation uses:

```text
/home/lqy/.config/robopara/p-150gk23k-718446bc2ebd.json
```

Override that location with `--credentials_path` or the
`GOOGLE_APPLICATION_CREDENTIALS` environment variable. The default Vertex
project is `p-150gk23k` and the default location is `global`; override them with
`--vertex_project` / `--vertex_location` or `GOOGLE_CLOUD_PROJECT` /
`GOOGLE_CLOUD_LOCATION`. Never commit a service-account file.

The simulation uses the official `google-genai` SDK and sends each assembled
prompt through `client.models.generate_content()`. The returned text continues
to pass through the existing action parser.

## Usage 
### Run a minimal Sort task with Gemini 3.7 Flash
```
$ conda activate roco
(roco) $ python run_dialog.py --task sort --llm_source gemini-3.7-flash --credentials_path /path/to/service-account.json --vertex_project p-150gk23k --vertex_location global --temperature 0 --comm_mode plan --tsteps 1 --num_replans 1 --no_feedback --skip_display --run_name gemini_smoke
```

On a headless Linux machine, prefix the command with `MUJOCO_GL=egl` so MuJoCo can render camera observations without a desktop display.

The command writes prompts, plans, state snapshots, and execution videos under `data/gemini_smoke/`. After it succeeds, remove `--no_feedback`, increase `--tsteps`, and increase `--num_replans` for a full episode.


## Contact
Please direct to [Mandi Zhao](https://mandizhao.github.io). 
If you are interested in contributing or collaborating, please feel free to reach out! I'm more than happy to chat and brainstorm together. 

## Cite
```
@misc{mandi2023roco,
      title={RoCo: Dialectic Multi-Robot Collaboration with Large Language Models}, 
      author={Zhao Mandi and Shreeya Jain and Shuran Song},
      year={2023},
      eprint={2307.04738},
      archivePrefix={arXiv},
      primaryClass={cs.RO}
}
```
