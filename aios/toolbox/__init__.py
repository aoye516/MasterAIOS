"""Toolbox sub-agent: 高德全家桶（天气/路线/路况/POI/地理编码）+ mini-tools（计算器/单位换算/时区）。

无状态为主，唯一持久化的是 `places` 表（用户常用地点别名 → 经纬度+adcode）。
其它高德查询一律实时调 API，不缓存。
"""
