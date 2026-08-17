# 02 — Software Engineering Practices

Builds on [01-fundamentals.md](01-fundamentals.md). General software-craft principles and the
DevOps/architecture toolbox, independent of ML or GenAI.

## Core Coding Principles

Solid principles are the backbone of clean, maintainable, and scalable software.

> **Original Source:** [16 Software Engineering Principles I Ignored for Too Long](https://medium.com/pythoneers/16-software-engineering-principles-i-ignored-for-too-long-a69d32f1a52e)

| #  | Principle                                           | Description                                                                                                 |
| -- | --------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| 1  | **DRY**<br>Don't Repeat Yourself                    | Eliminate duplication using functions, classes, or abstractions. Reduces bugs and eases upkeep.             |
| 2  | **KISS**<br>Keep It Simple, Stupid                  | Opt for the simplest solution that works. Avoid unnecessary complexity.                                     |
| 3  | **YAGNI**<br>You Aren't Gonna Need It               | Implement only what's needed now. Don't build speculative features or abstractions.                         |
| 4  | **Separation of Concerns**<br>Single Responsibility | Each module, class, or function should have one clear purpose. Improves focus and testability.              |
| 5  | **Open-Closed Principle**                           | Code should be open for extension but closed for modification. Add features without breaking existing code. |
| 6  | **Liskov Substitution Principle**                   | Subtypes must be usable in place of their base types without altering program correctness.                  |
| 7  | **Interface Segregation & Dependency Inversion**    | Use small, specific interfaces. Depend on abstractions, not concrete implementations.                       |
| 8  | **Composition Over Inheritance**                    | Prefer assembling behavior by combining objects over deep inheritance trees. Increases flexibility.         |
| 9  | **Code for Humans**                                 | Prioritize readability, meaningful names, and straightforward logic. Clever code is often problematic.      |
| 10 | **Test Early & Often**                              | Write tests from the start (unit, integration, etc.). Early testing catches issues sooner and cheaper.      |
| 11 | **Refactor Continuously**                           | Regularly improve and simplify code. Prevents technical debt and code decay.                                |

### Why These Principles Matter

Adhering to these principles leads to:

* **Reduced technical debt** – Less copy-paste, fewer mysterious bugs, and simpler codebases
* **Greater maintainability and scalability** – Code remains understandable as projects and teams grow
* **Fewer bugs and faster debugging** – Clear responsibilities and good tests make issues easier to find and fix
* **Improved collaboration** – New team members (or your future self) can read and extend code with less friction

Neglecting these principles can quickly turn small projects into unmanageable messes. Time saved by "quick fixes" is often lost many times over in future debugging and rewrites.

Write code as if you'll be the next person maintaining it—months later, under pressure. These principles are your shortcut to a smoother, less painful future.

---

## Development Standards

* **Standardized Python Environment**
  Use reproducible templates (e.g., cookiecutter) integrating pre-commit hooks, Makefile, and unified tooling. See [01-fundamentals.md](01-fundamentals.md).

* **Reusable Python Packages**
  Provide core packages for shared functionality, with consistent integration of development tools.

* **Environment-Based Configuration**
  Manage application settings via environment variables, validated at startup.

* **Container-Oriented Architecture**
  Utilize Docker or Kubernetes for consistent deployment environments.

* **Documentation & Testing**
  Ensure all modules, packages, and APIs are well-documented and covered by automated tests.

---

## Tools & Best Practices

* **Python Code Quality:** linters, formatters (black, flake8, pylint — largely superseded by ruff, see [01-fundamentals.md](01-fundamentals.md))
* **Version Control:** **Git Flow is the standard branching strategy** for collaborative
  development in this repo (trunk-based development is the lighter-weight alternative for
  smaller teams). No direct pushes to the main branch — every change lands via a PR/MR opened
  against the repo.
* **CI/CD:** Automated pipelines for testing, linting, and deployment
* **Secrets Management:** Keep credentials outside the repository (use environment variables or vaults)
* **Code Reviews:** Ensure readability, maintainability, and adherence to principles
* **Refactoring:** Regularly improve structure without changing behavior
* **Modular Design:** Break applications into small, testable, and reusable components
* **Monitoring & Logging:** Instrument applications to detect issues early

---

## Architecture & DevOps Toolbox

### Diagramming

* **excalidraw:** https://excalidraw.com
* **swimlanes.io:** https://swimlanes.io
* **draw.io:** https://app.diagrams.net

### CI / CD

* **Jenkins:** [Getting started with the Guided Tour](https://www.jenkins.io/doc/pipeline/tour)
* **GitLab CI/CD:** https://docs.gitlab.com/ee/ci/
* **GitHub Actions:** https://docs.github.com/en/actions

### Container Orchestration (Kubernetes)

* **Kubernetes Basics (official tutorial):** https://kubernetes.io/docs/tutorials/kubernetes-basics/
* **minikube** (local single-node cluster for learning/dev): https://minikube.sigs.k8s.io/docs/start/

### Infrastructure as Code (IaC)

* **Terraform:** https://developer.hashicorp.com/terraform
* **CloudFormation (AWS):** https://aws.amazon.com/cloudformation/getting-started

### Cloud & DevOps Extras

* **LocalStack:** https://github.com/localstack/localstack
* **Coder:** https://github.com/coder/coder

### API / Testing Tools

* **Swagger Editor:** https://editor.swagger.io/
* **Postman:** https://www.postman.com/
* **JWT.io:** https://jwt.io
* **Lens (K8s):** https://lenshq.io/

---

## References & Further Reading

* [Efficient Python for Data Scientists](https://github.com/youssefHosni/Efficient-Python-for-Data-Scientists)
* [Python Code Quality: Tools & Best Practices – Real Python](https://realpython.com/python-code-quality/)
* [Five Tips to Elevate the Readability of Your Python Code | Towards Data Science](https://towardsdatascience.com/five-tips-to-elevate-the-readability-of-your-python-code-7b049bbf72e6)
* [Coding 102: Writing code other people can read – Stack Overflow Blog](https://stackoverflow.blog/2023/02/13/coding-102-writing-code-other-people-can-read/)
* [Design Patterns in Python](https://refactoring.guru/design-patterns/python)
* [Managing Python Projects With uv – Real Python](https://realpython.com/python-uv/)
