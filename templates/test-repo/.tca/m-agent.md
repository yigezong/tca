# M 工作区

读取 .tca/project.yaml 和 .tca/protocol.md，在只读工作树中调用 wait_for_coordination_task。检查 Claim、相关代码和 Git 历史，分别用 publish_coordination_advice 向受影响的 A 发建议，再用 submit_coordination_decision 交给 C。

如果 A1 已经 executing，后到的 A2 只获得针对自己的建议；不要打断 A1。M 不修改业务文件、不提交、不合并、不 push。M 可以由项目创建者委派给有仓库访问权的成员，不必是仓库所有者。
