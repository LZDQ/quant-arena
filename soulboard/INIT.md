按照以下步骤进行初始化：
1. 运行 `pwd` 查看当前目录。
2. 运行 `which uv` 查看是否配置好了 `uv` 路径。如果没有，可以使用 `/opt/uv/uv`。
3. 检查当前目录，包括隐藏文件。如果当前目录下没有虚拟环境 `.venv/`，运行 `uv venv` 在当前目录下创建。
4. 阅读 `/market-data/README.md` 了解如何获取实时数据，并在虚拟环境中安装需要的包。
5. 查看 cron jobs。如果没有，按照指示创建它们。
6. 使用 quant-arena MCP 查询初始资金。如果没有这个 MCP，你需要立即汇报。

后续运行 python 需要用这种方式激活虚拟环境：
```sh
source .venv/bin/activate && python ...
```
