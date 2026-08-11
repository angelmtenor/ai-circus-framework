# Contributing Guidelines

Thank you for considering contributing to this project! We welcome all contributions, whether it's reporting bugs, suggesting enhancements, or submitting code changes. These guidelines aim to ensure a smooth review and integration process while maintaining a welcoming and inclusive environment for all contributors.

Before you start, please take a moment to read our [Code of Conduct](CODE_OF_CONDUCT.md). It outlines our expectations for participation and helps ensure a positive experience for everyone.

## How to Contribute

If you're new to contributing, don't worry! We're here to help. Here are the general steps to follow:

1. **Discuss the Change**: Before making significant changes, it's a good idea to discuss them with the project maintainers. You can do this by opening an issue or starting a discussion.
2. **Review Guidelines**: Read [AGENTS.md](AGENTS.md) and [SKILLS.md](SKILLS.md) for architectural and testing guidelines — they apply whether you're a human contributor or an AI coding agent.
3. **Fork the Repository**: Create a fork of this repository on GitHub.
4. **Clone Your Fork**: Clone your forked repository to your local machine.
5. **Create a Feature Branch**: Create a new branch for your changes.
6. **Set Up Environment**: Run `make setup` to initialize your environment.
7. **Make Changes**: Implement your changes, following the project's coding standards and guidelines.
8. **Run Tests and Checks**: Ensure your changes pass all quality checks and tests using `make check`.
9. **Full Verification**: Before committing, run `make all` to verify the entire pipeline (clean -> setup -> check -> run).
10. **Commit Your Changes**: Commit your changes with clear and descriptive messages following [styleguide.md](styleguide.md).
11. **Push Your Branch**: Push your feature branch to your fork on GitHub.
12. **Open a Pull Request**: Open a pull request from your feature branch to the main branch of the original repository.
13. **Follow Up**: Respond to any feedback or requests for changes from the maintainers.

## Setting Up Your Environment

To set up your local development environment:

1. **Fork the repository** on GitHub.
2. **Clone your fork**:
   ```bash
   git clone https://github.com/angelmtenor/ai-circus-framework
   ```
3. **Move into this service** (this is a monorepo — every service lives under `services/`):
   ```bash
   cd ai-circus-framework/services/training
   ```
4. **Run setup**:
   ```bash
   make setup
   ```
5. **Create a feature branch**:
   ```bash
   git checkout -b descriptive-feature-name
   ```

## Code Quality and Testing

- Run quality checks (linting, formatting):
  ```bash
  make qa
  ```
- Run tests:
  ```bash
  make test
  ```
- Combined check:
  ```bash
  make check
  ```
- End-to-end verification:
  ```bash
  make all
  ```

## Pull Request Process

When opening a pull request, please:

- Ensure any install or build dependencies are removed before the end of the layer when doing a build.
- Update the README.md with details of changes to the interface, including new environment variables, exposed ports, useful file locations, and container parameters.
- Increase the version numbers in any examples files and the README.md to the new version that this Pull Request would represent. The versioning scheme we use is [SemVer](http://semver.org/).

## Code of Conduct

By participating in this project, you agree to abide by its [Code of Conduct](CODE_OF_CONDUCT.md).
