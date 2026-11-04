# MaaFGO 编队与战斗编辑器前端

玩家使用软件根目录的 CMD 或 MXU 启动任务，无需 Node.js 或联网。
本目录保存源码；发布静态产物位于 `assets/web/battle-editor/`。

第三方许可正文统一维护于 `public/THIRD_PARTY_NOTICES.md`。每次 Vite 构建
会自动把 `public/` 内容复制进发布目录，保证许可与页面一起打包分发。

```sh
npm ci
npm test
npm run build
```

开发服务器使用 `npm run dev`。修改 `vite.config.ts` 的本地代理端口以匹配
测试用编辑服务。生产部署的静态页面与 `/api`、`/media` 同源，不使用 CDN。

数据以完整 Chaldea JSON 树为主，编辑只更新明确字段。`model.ts` 提供回合
投影、当前阵容回放、换人索引配对、真实牌组索引选择及保真操作；自动草稿
与编辑器页面状态分开保存在服务端。

支持的字段与边界以仓库制作方案、服务端校验报告为准。未支持动作保留原数据，
可以下载保真副本；正式保存必须通过当前执行能力检查。
