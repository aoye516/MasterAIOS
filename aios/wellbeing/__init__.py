"""Wellbeing sub-agent: 起居 / 习惯打卡 / 健康指标 / 每日早间播报。

三大能力：
- morning-brief: 规则化生成"今天天气 + 穿衣建议 + 个人健康提醒"，零 LLM 成本
- habits / habit-checkins: 重复性打卡（晨跑、吃药、喝水、拉伸）+ streak 计算
- health_logs: 数值型健康指标时序（体重、尿酸、血压、睡眠）
"""
