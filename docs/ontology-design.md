# Ontology 设计

Ontology 只保存 TBox：`ClassDef`、`PropertyDef`、`RelationDef`、`OntologyAxiom`、`OntologyVersion`。运行时实例（Alarm、Device、Execution）属于 ABox/业务数据，不写入 TBox。

`OntologyVersion` 使用 `MAJOR.MINOR.PATCH`。兼容变更提升 MINOR/PATCH；破坏性 TBox 变更提升 MAJOR，并通过 `compatible_from` 标记最低兼容版本。
