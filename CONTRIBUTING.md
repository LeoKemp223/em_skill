# 贡献指南

## 范围

这个仓库存放面向嵌入式固件开发的 Codex skill 及共享参考资料。新的提交应当改善以下至少一项：

- 可复用的嵌入式工作流
- 共享约定或决策规则
- 被多个 skill 复用的参考文档
- 用于保证仓库一致性的校验工具

## 技能命名

- 仅使用小写字母、数字和连字符。
- 优先选择动作导向的名字，例如 `flash-openocd` 或 `fault-triage`。
- 目录名必须与 frontmatter 中的 skill `name` 保持一致。

## 技能结构

每个 skill 必须包含一个 `SKILL.md`，并带有以下章节：

- `## 适用场景`
- `## 必要输入`
- `## 自动探测`
- `## 执行步骤`
- `## 失败分流`
- `## 平台说明`
- `## 输出约定`
- `## 交接关系`

不要从空白文档开始写，直接复用 [templates/skill-template/SKILL.md](/home/leo/work/open-git/em_skill/templates/skill-template/SKILL.md)。
在认定一个 skill 已经完成前，请用 [templates/skill-template/SCENARIOS.md](/home/leo/work/open-git/em_skill/templates/skill-template/SCENARIOS.md) 定义最小覆盖范围。

## 内容准则

- 共享策略应写入 `shared/`，不要只写在某个单独 skill 中。
- 示例保持简短且具体。
- 当一个 skill 可能在多个配置或工具之间做选择时，必须写清优先级规则。
- 只有在默认值足够安全时才允许定义默认行为，否则应阻塞并要求澄清。
- Windows、macOS、Linux 的路径差异应作为共享参考资料维护，而不是散落在各 skill 内。

## 校验

提交前请先执行：

```bash
python3 scripts/validate_repo.py
```

如果你新增了必需章节或文件，也要在同一提交中同步更新校验器。
