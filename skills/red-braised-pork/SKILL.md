---
name: red-braised-pork
description: 制作红烧肉的 SOP，接到订单后依次调用内置厨具工具完成出品
---

# red-braised-pork SOP

接到 cook_dish 确认后，依次调用内置工具完成制作：

1. prep_tool   → 切肉、备香料
2. fry_tool    → 炒糖色 180°C
3. cook_tool   → 焖煮 40min
4. plate_tool  → 装盘出品

四步完成后报告"红烧肉完成"。
