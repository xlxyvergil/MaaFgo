# 速通模式说明

## 适用场景

- 想让部分任务**在特定周期内限次数运行**，避免重复浪费资源。
- 典型用途：每日/每周/每月只运行一次的活动任务或节奏任务。
- 需要通过 UI/CLI 将 `speedrun_mode` 明确打开，才会启用速通逻辑。

## 配置位置

在 `interface.json` 中对应任务下添加一个 `speedrun` 块，例如：

```jsonc
{
  "name": "纷争战区",
  "entry": "纷争战区",
  "speedrun": {
    "mode": "weekly",
    "trigger": {
      "weekly": {
        "weekday": [2],
        "hour_start": 5
      }
    },
    "run": {
      "count": 2,
      "min_interval_hours": 24
    }
  }
}
```

## 字段说明

- `mode`: 周期类型，支持 `"daily"`、`"weekly"`、`"monthly"`。
- `trigger`: 触发细节。
  - `"daily"` 只需配置 `hour_start`，即每天几点后刷新次数。
  - `"weekly"` 可配置 `weekday`（1=周一...7=周日）和 `hour_start`。
  - `"monthly"` 配置 `day`（几号）和 `hour_start`。
- `run.count`: 单个周期内允许运行的次数，设置为 `null`、`-1` 或省略表示无限制。
- `run.min_interval_hours`（可选）：要求两次运行间隔至少多少小时。

## 行为流程

1. 任务运行时只在 `speedrun_mode` 打开 && `run.count` 有效（非 `null`/`-1`）时生效。
2. 读取 `_speedrun_state.last_runtime`（首次缺省为 `1970-01-01T00:00:00`），计算下一个刷新点（例如下一个满足规则的 5 点）。
3. 当前时间超过刷新点时，会把 `remaining_count` 刷新为 `run.count`；否则继续使用旧值。
4. 若剩余次数为 0，则跳过并输出 `"本周期内剩余执行次数为0"`。
5. 满足 `min_interval_hours` 后，才能继续消耗次数并运行。
6. 任务成功后：记录最新时间为 `last_runtime`，`remaining_count -= 1` 并保存。

## 示例：每天 5 点后的首次运行

```jsonc
"speedrun": {
  "mode": "daily",
  "trigger": {
    "daily": {
      "hour_start": 5
    }
  },
  "run": {
    "count": 1,
    "min_interval_hours": 24
  }
}
```

每天 05:00 后只会运行一次，`count` 重置后会写入 `last_runtime`，下次刷新时刻到达前查到 `remaining_count=0` 就跳过。
