# Git Commit Message Style Guide
Applies repo-wide (root docs and every generated service under services/, ui-react/).

## Overview
This guide defines the conventions for writing Git commit messages in this project. It aligns
with the [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) specification to
ensure clarity, consistency, and maintainability in the project's version history. Well-crafted
commit messages facilitate collaboration, debugging, and automation (e.g., generating changelogs).

## Message Structure
A commit message consists of three parts:
- **Type**: Subject
- **Body** (Optional)
- **Footer** (Optional)

**Important Format: type: subject**

- Begin your commit message with a valid type (e.g., feat, fix, docs) followed by a colon and a space.
- The subject should be a concise, imperative summary (ideally ≤50 characters) that starts with a capital letter and avoids ending punctuation.

Example:
• feat: Add health-check endpoint

### Type
The type indicates the nature of the change. Use one of the following:
- `feat`: A new feature
- `fix`: A bug fix
- `docs`: Changes to documentation (e.g., updating README or docstrings)
- `style`: Formatting or code style changes (e.g., applying PEP 8) with no functional change
- `refactor`: Refactoring production code (e.g., restructuring code for clarity)
- `test`: Adding or refactoring tests (e.g., unit tests) with no production code change
- `setup`: Updating build tasks or package configurations (e.g., updating `pyproject.toml`)
- `release`: Creating a new release (e.g., publishing a Python package)
- `perf`: Performance improvements (e.g., optimizing a query or algorithm)
- `revert`: Reverting a previous commit (e.g., undoing a problematic change)
- `clean`: Cleaning up code (e.g., removing unused files, functions, comments)

### Subject
The subject is a concise summary of the change. It must:
- Be no longer than 50 characters
- Begin with a capital letter
- Not end with a period
- Use an imperative tone (e.g., "Add", "Fix", "Update")

### Body (Optional)
- Use the body to provide additional context for complex changes or to explain the reasoning behind the change.
- Wrap lines at 72 characters for readability.
- Include details such as:
  - Why the change was made
  - What components are affected
  - Any trade-offs or considerations

### Footer (Optional)
- Use the footer for metadata, such as:
  - Issue references (e.g., "Closes #123")
  - Breaking changes (e.g., "BREAKING CHANGE: description")
  - Other notes or references
- Indicate breaking changes with a `!` after the type (e.g., `feat!`) or a "BREAKING CHANGE" footer.

## Best Practices
- **Imperative Mood**: Write subjects in the imperative mood (e.g., "Add feature" instead of "Added feature").
- **Clarity and Conciseness**: Ensure the subject is descriptive enough to understand the change at a glance.
- **Consistency**: Adhere to the defined types and structure for all commits.
- **When to Use a Body**: Include a body for:
  - Complex changes requiring explanation
  - Changes with significant impact
  - Providing context for code reviewers
- **Breaking Changes**: Clearly mark breaking changes using `!` or a "BREAKING CHANGE" footer to alert team members.
- **Issue Tracking**: Reference issue numbers in the footer (e.g., "Closes #123") if using an issue tracker like GitHub Issues.

## Examples
### Basic Examples
```
• feat: Add retry logic to the example service client
• docs: Add instructions for running the Docker container
• refactor: Move shared HTTP helpers into core/
• test: Add coverage for config drift detection
• fix: Handle missing EXAMPLE_SERVICE_API_KEY gracefully
• perf: Reduce cold-start time of the settings loader
• revert: Revert "feat: Add caching layer" due to regression
```

### Extended Example with Body and Footer
```
feat: Add new authentication mechanism

This commit introduces an OAuth2-based authentication method, replacing the deprecated basic auth system. The new method improves security and aligns with modern standards. Existing users will need to update their credentials.

BREAKING CHANGE: The old authentication method is deprecated and will be removed in the next major release.
Closes #456
```

## Benefits
- **Collaboration**: Clear messages help team members understand changes, improving code reviews.
- **Debugging**: Detailed commit messages make it easier to trace bugs or changes in the project history.
- **Automation**: Structured messages support tools that generate release notes or changelogs.
- **Onboarding**: Informative commit messages help new developers understand the project's evolution.

## Credits
This guide incorporates principles from the [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) specification.
