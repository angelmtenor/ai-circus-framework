# 03 — Machine Learning & Data Science

Builds on [01-fundamentals.md](01-fundamentals.md) and [02-software-engineering.md](02-software-engineering.md).
Core ML/DS concepts, workflow discipline, explainability, and the Python DS toolbox.

## Learning Resources

### Courses & Tutorials

* [Applied Data Science with Python – University of Michigan](https://www.coursera.org/specializations/data-science-python)
* [Kaggle Learn](https://www.kaggle.com/learn/overview)
* [Kaggle: Machine Learning Explainability](https://www.kaggle.com/learn/machine-learning-explainability) — recommended, pairs with the [Explainability](#ml-explainability--interpretability) section below.

### Books

* **Deep Learning** (Goodfellow): [deeplearningbook.org](https://www.deeplearningbook.org/)
* **Deep Learning with Python** (Chollet)

### Online Guides

* [Google ML Guides](https://developers.google.com/machine-learning/guides/)

---

## Core ML / DS Principles

* Start simple, establish baseline quickly
* Never touch test data during feature engineering
* Use stratified splits for imbalanced data
* Prefer permutation importance or SHAP over tree impurity importance
* Document all experiments, including failures

### ML Concepts

| Concept                   | Definition                                              |
| ------------------------- | ------------------------------------------------------- |
| **Supervised Learning**   | Predict output given input-output examples              |
| **Unsupervised Learning** | Derive structure from unlabeled data                    |
| **Feature Scaling**       | Normalize inputs for faster gradient descent            |
| **Regularization**        | Control model complexity (L1/L2) to prevent overfitting |
| **Overfitting**           | Fits training data but fails on new data                |
| **Underfitting**          | Model too simple; high bias                             |

### Train / Validation / Test Split

* Optimal: 60% train, 20% validation, 20% test
* Minimal: 70% train, 30% test

### Neural Networks

* Binary classification → Sigmoid
* ReLU networks → He initialization
* Batch normalization improves optimization
* Embeddings: `dimension ≈ √(possible_values)`
* Missing values → zero input

### Error Analysis

1. Start simple; test early on validation data
2. Plot learning curves
3. Manually analyze misclassified examples
4. Extract new features from error patterns

---

## ML Workflow / Best Practices

### Data Handling

* Leakage prevention: hide test set; scale inside CV folds
* Imbalanced data: use F1, stratified splits, class_weight, oversample minorities
* Interpretability: SHAP, permutation importance, ablation studies

### Experimentation

* Monitor input features; version model configuration
* Document all experiments (including failures)
* Start simple, observe metrics, iterate

### Security & Credentials

* Keep credentials **outside** project repo
* Load keys from environment variables (use `cryptography` library)

```python
from cryptography.fernet import Fernet

key = os.getenv("ENCRYPTION_KEY")  # Never hardcode
cipher = Fernet(key)
decrypted = cipher.decrypt(encrypted_data)
```

---

## ML Explainability & Interpretability

### Explainability vs. Interpretability

While often used interchangeably, these terms are distinct:

* **Interpretability:** The extent to which one can observe cause and effect in a system.
  *Intuition:* You understand how the model works mechanically, without necessarily knowing *why*.

* **Explainability:** The extent to which a model's internal mechanics can be described in human terms.
  *Intuition:* You can explain *why* a model made a specific decision.

**Analogy:** A chemistry experiment is **interpretable** because you can see the process (mixing chemicals) and outcome (color change). It becomes **explainable** when you understand the molecular interactions behind the reaction.

### Key Techniques for Model Inspection

#### Feature Importance

Measures each feature's contribution to model predictions.

**A. Permutation Feature Importance (Model-Agnostic)**

*Steps:*
1. Train model and compute baseline score (accuracy, R², etc.).
2. Shuffle one feature's values in validation data.
3. Recompute model score.
4. Difference from baseline = feature importance.

*Interpretation:* Large drop → feature is critical; small drop → feature less important.

**Caveat:** Correlated features can dilute importance. Solution: cluster correlated features and use one representative per cluster.

**B. Impurity-Based Importance**

*Default for tree models (e.g., Random Forest).* Can be biased toward high-cardinality features. Permutation importance is often more reliable.

### Tools & Frameworks

* **LIME:** Explains predictions locally by fitting a simple, interpretable model around a data point.
* **SHAP:** Game-theoretic approach providing consistent, locally accurate feature attributions.
* **ELI5:** Python library for inspecting and debugging ML models.
* **InterpretML:** Includes Explainable Boosting Machine (EBM), an interpretable GAM.

### References & Resources

**Reading**
* [KDnuggets: Explainability vs Interpretability](https://www.kdnuggets.com/2018/12/machine-learning-explainability-interpretability-ai.html)
* [Interpretable Machine Learning by Christoph Molnar](https://christophm.github.io/interpretable-ml-book/shap.html)
* [Ethical OS Principles](https://ethical.institute/principles.html#commitment-3)

**Tutorials & Guides**
* Kaggle: Machine Learning Explainability — see [Learning Resources](#learning-resources) above.
* [Scikit-Learn: Permutation Importance](https://scikit-learn.org/stable/modules/generated/sklearn.inspection.permutation_importance.html#sklearn.inspection.permutation_importance)
* [Handling Correlated Features](https://scikit-learn.org/stable/auto_examples/inspection/plot_permutation_importance.html)

**Libraries**
* [SHAP](https://github.com/slundberg/shap)
* [ELI5](https://github.com/TeamHG-Memex/eli5)
* [InterpretML](https://github.com/interpretml/interpret)
* [Awesome Production ML](https://github.com/EthicalML/awesome-production-machine-learning)

---

## Python Data Science Libraries

### Core Libraries

| Category | Libraries |
|--------|-----------|
| **Deep Learning (DL)** | PyTorch, Keras |
| **Machine Learning (ML)** | scikit-learn, shap, shapiq |
| **Natural Language Processing (NLP)** | nltk, gensim, gluonnlp |
| **Data Handling & Manipulation** | pandas, numpy, polars, collections, re (regex), datetime, pickle, networkx |
| **Visualization & Plotting** | seaborn, matplotlib, pandas plot, plotly, missingno |
| **Web Apps & Dashboards** | streamlit |
| **Web & APIs** | fastapi, beautifulsoup4 |
| **Data Validation & Models** | pydantic, sqlmodel |
| **Workflow Orchestration** | prefect |
| **Databases / Analytics Engines** | duckdb |
| **Browser Automation / Scraping** | playwright |
| **CLI Tooling** | typer |
| **Scaling / Parallelism** | dask, swifter |
| **Generic Utilities** | random, time, os, pathlib, warnings |
| **Cryptography / Security** | cryptography |

### Additional Useful Libraries

* **MLflow:** ML lifecycle platform
* **Optuna:** Hyperparameter optimization
* **Talos / Hyperas:** Hyperparameter scanning for Keras
* **kerasplotlib / livelossplot:** Training visualization
* **autokeras:** Automated ML → http://autokeras.com/
* **missingno:** Visualize missing data
* **fancyimpute:** Advanced imputation
* **chardet:** Detect text encoding
* **fuzzywuzzy:** String similarity
* **ludwig:** Declarative deep learning
* **Finetune:** Scikit-learn style finetuning for NLP
