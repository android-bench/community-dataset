Hi! Thank you for your interest in contributing to Android Bench!

**Why should you contribute?**

We want to create a leading benchmark in Android related tasks so that we incentivise Large Language Models improvement in Android development.
Because of that we need high quality, realistic tasks that provide nuanced, fresh and hard problems for the LLMs to solve. 

**Want to help?**

This guide will walk you through the whole process of creating such a task. If you're not ready to implement a task yet, check out [other ways to contribute](#other-ways-to-contribute).

> [!IMPORTANT]
> Successful task submission does not guarantee that it will be merged with the official Android Bench dataset. 

# Table of Contents
- [Before you start](#before-you-start)
- [Propose your task](#propose-your-task)
- [Create your task](#create-your-task)
- [Setup environment](#setup-environment)
- [Initialize the task](#initialize-the-task)
- [Implement the task](#implement-the-task)
- [Test the task](#test-the-task)
- [Submit the task](#submit-the-task)	
- [After the submission](#after-the-submission)
- [Other ways to contribute](#other-ways-to-contribute)
- [FAQ](#faq)

# Before you start 
Before you start make sure you understand [what is a task](docs/anatomy-of-a-task.md) and what [makes it good](docs/task-guideline.md). 

We want you to have complete freedom in terms of used repositories and area of your interest. However, if you want some inspiration here are the [areas that we want to focus on and repositories that we used](docs/areas-and-repositories.md).

## Harbor integration
We are using [harbor framework](https://www.harborframework.com/docs) for this process and we highly recommend getting familiar with it before starting.

# Propose your task
First step is pitching your idea in the [repository discussion called "Task Proposal"](https://github.com/android-bench/community-dataset/discussions/categories/task-proposals).
The proposal is prefilled with a template to make sure all the information needed is given.

Every proposal goes through two type of reviews:
1. **Automatic review:** this review is triggered whenever you start the "Task Proposal" discussion or enter `/re-review` in the comment. The review is used as a guideline, and is not a final decision.
2. **Human review:** one of human experts reviews the task and reads automatic review. If the idea is accepted by the human reviewer you can start implementing the task.

Depending on the proposal there might be a discussion or need for change before it is accepted into the next steps.

# Create your task

## Setup Environment
### Install tools
#### 1. Install Harbor.
Install [Harbor](https://github.com/laude-institute/harbor), framework used by us for evaluating agents. Verify it is running.
```
uv tool install harbor
harbor --version
```
#### 2. Install Docker.
Install [Docker](https://www.docker.com/products/docker-desktop/), verify it is running:
```
docker ps
```
#### 3. Install Dataset V2 CLI (`v2.task`).
The `v2.task` CLI provides tools for creating task structure, building Docker containers, refreshing patch goldens, and verifying Dataset V2 tasks.

Install the repository tools using `uv`:
```bash
uv pip install -e .
```
Or run the CLI directly without installation:
```bash
uv run v2.task --help
```

Available subcommands:
* `v2.task create`: Interactive wizard (or manual flags) to scaffold fresh tasks.
* `v2.task docker`: Generate Dockerfiles and build container layers.
* `v2.task refresh-patches`: Calculate canonical solution and test patches.
* `v2.task verify-tests`: Determine task test results inside Docker containers.

For detailed subcommand parameters and advanced workflows, see [v2/README.md](v2/README.md).

### Setup task environment
Fork the repository you want to work on or create your own repository that will be used for task implementation.

### Setup this repository
#### 1. Fork this repository.
#### 2. Clone and go to your fork.

In you terminal - go to the directory you want to place the repository in and run following commands:
```
git clone https://github.com/<YOUR_GITHUB_USERNAME>/community-dataset.git
cd community-dataset
```

## Initialize the task.
#### 1. Create a branch for your task.
```
git checkout -b <branch-name>
```
#### 2. Initialize the task.
Scaffold a Dataset V2 task using the interactive CLI wizard:
```
uv run v2.task create
```
Or initialize directly with Harbor:
```
harbor tasks init <task-name> --include-canary-strings --metadata-template task-template.toml -p tasks/
```

This command will provide a structure and ensure that canary strings were added  to prevent data contamination. 

## Implement the task

### Pre-fill required fields
Fill in the required task files with appropriate data. Start with:
- Adding your instruction to: `tasks/<task-name>/instruction.md`
- Defining the environment by implementing the Dockerfile: `tasks/<task-name>/environment/Dockerfile`
- Using the test script to generate a reward: `tasks/<task-name>/tests/test.sh`
- Filling out the solution: `tasks/<task-name>/solution/solve.sh`

More about files and task structure in the [anatomy of a task](docs/anatomy-of-a-task.md) section.

### Create and implement solution
### Generate necessary patches
After you'll implement the task you need to generate patches for solution and tests.

In your tasks repository, copy and paste following commands to generate files.

```
git diff <before_sha> <after_sha> -- . ':(exclude)app/src/test/*' ':(exclude)app/src/androidTest/*' > solution.patch

git diff <before_sha> <after_sha> -- app/src/test/* app/src/androidTest/* > test.patch
```
Move those files into `solution/` and `tests/` folder inside your task.
	
## Test the task
Before submitting the task make sure that the task works and the quality checks are met.

- Set up API Keys
- Validate task quality
- Run the Oracle agent (`-a oracle`)
- Test on AI agent

> [!NOTE]
> If your solution passes Gemini 2.5 Flash model it is too easy.

## Submit the task
Before submitting the task follow the [implementation checklist](docs/task-implementation-checklist.md) to make sure your task is ready for review.

## After the submission
After the submission automated CI Validation script will run the oracle agent.
Maintainers will review your task against the review guidelines.

> [!NOTE]
> Expect that back-and-forth feedback on your task is possible before the final merge. It is a normal part of making sure that the tasks follow high quality standards.

# Other ways to contribute
If you're not ready to implement a task yet, there are many other ways you can help:
- **Propose task ideas**: Share your thoughts for potential tasks in the [Task Proposal discussion](https://github.com/android-bench/community-dataset/discussions/categories/task-proposals). You don't need to implement them yourself!
- **Promote the repository**: Help us grow the community by sharing Android Bench with your colleagues, starring the repository, or writing about it.
- **Give feedback**: Use the project and let us know what you think! You can report bugs, suggest improvements to the documentation, or share your overall experience in the [Q&A discussion forum](https://github.com/android-bench/community-dataset/discussions/categories/q-a).

## FAQ
If any questions arise during the process feel free to ask in the [Q&A discussion forum](https://github.com/android-bench/community-dataset/discussions/categories/q-a).
