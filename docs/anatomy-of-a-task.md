# Explenation
Tasks are designed to simulate how human Android developers work in real-world codebases. This means they present agents with genuine issues, full project context, and realistic testing workflows rather than isolated, artificial puzzles.

# Anatomy of a task
Every task needs to be created inside the `tasks` folder and follow [harbor task structure](https://www.harborframework.com/docs/tasks).

A complete task is usually made of following files:
```
tasks/
├── {task_id}/
│   ├── task.toml                 # task metadata
│   ├── instruction.md            # instructions
│   ├── environment/              
│   │   └── Dockerfile           # task docker image
│   │   └── docker-compose.yaml  # docker compose (can be multiple images)
│   ├── solution/                
│   │   ├── solution.patch       # oracle/golden solution
│   │   └── solve.sh             # applies the solution.patch
│   └── tests/
│       ├── test.sh               # Evaluation script
│       └── test.patch            # Patch containing tests to validate agent's fix
│       └── validate.sh           # Script that validates correct solution

```

### task.toml
The configuration file containing task metadata, before/after commit SHAs, build/test commands, and acceptance criteria.

Here you store all the information about the author, difficulty, hardware limits and timeout boundaries. It also includes [verifier.env] section used to securely inject API keys into the environment. 

More details about the configuration parameters is available in [harbor documentation](https://www.harborframework.com/docs/tasks#configuration--metadata).

### intruction.md
Task instruction for the agent. 

While writing the instructions keep in mind to:
- Write the file yourself
- Be explicit about the output and its format
- Include all of the information that the agent needs to complete the task and nothing else

### README.md (Optional)
Additional development context that wasn’t passed in other files. 

Any information that you find useful for a reviewer and future maintainers should go here (design docs, visualizations, external links, ideas for future roadmap etc.). 

## Environment
The environment folder contains Dockerfile that is responsible for specifying the isolated container environment, with information such as the dependencies and packages that agent needs to execute your task.

If you need to provide an agent with any additional data this folder is the one to do it in. 
### Dockerfile
The environment definition used to run, build, and evaluate the specific codebase and task.
### docker-compose.yaml
Definition of all docker containers.
## Solution (Optional)
The solution folder must contain a `solution/solve.sh` script.
If no solution is provided, the Oracle agent cannot be used to sanity check the task.
### solution.patch
The reference solution patch that correctly resolves the issue described in the task.
### solve.sh
Oracle reference solution.
Optionally you can add python test files used by solve.sh.
## Tests
The tests folder contains a test suite to verify if the agent solution is correct.
It is run after the agent completes the task. 

The tests folder must contain a `tests/test.sh` script.
We recommend using absolute paths in your test script to avoid relative path issues.
### test.sh
Runs the tests directly and reports the result to `/logs/verifier/reward.txt`. It should also handle environment setup like `JAVA_HOME`.
Optionally you can add python test files used by test.sh.
### test.patch
The patch containing the tests that verify the issue is fixed.
### validate.sh
An optional script that is run after tests to evaluate conditions that are difficult to cover with standard Android tests.
