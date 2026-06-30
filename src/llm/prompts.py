"""LLM Prompt 模板 / LLM Prompt Templates — 按实体类型分."""  # noqa: E501

# ═══════════════════════════════════════════════════════════════
# System prompt — 通用世界创作助手
# ═══════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """你是一个世界创作助手，专门为 AIGameWorld 游戏引擎生成 YAML 配置内容。
必须遵循以下规则：
1. 输出格式必须是合法的 YAML，key-value 结构与提供的模板一致
2. 所有中文字段需要详尽描写，避免笼统概括
3. 数值字段（hp/ac/attributes等）根据角色定位赋予合理值
4. lore 内容需有深度和因果逻辑
5. 人物性格要有矛盾与成长空间，不能扁平化
6. NPC 的 function_data 要具体可交互"""

# ═══════════════════════════════════════════════════════════════
# Lore prompt
# ═══════════════════════════════════════════════════════════════

LORE_PROMPT = """请为名为 "{world_name}" 的世界生成 {count} 条世界观设定（lore）。
每条的 id 用序号编号，category 从以下选：geography, history, race, faction, culture, magic, religion。

输出格式（严格的 YAML，不要用 ```yaml 包裹）：
- id: lore_1
  category: history
  content: >
    详细的设定文本，至少3句话，要有具体的细节、人名地名、因果关系。不能是空洞的概括。 \
    用 > 表示多行文本，保持每行缩进一致。
  references: [scene_1]

- id: lore_2
  category: magic
  content: ...
  references: []

... 共 {count} 条

世界主题提示: {theme}"""

# ═══════════════════════════════════════════════════════════════
# NPC personalist/personality prompt
# ═══════════════════════════════════════════════════════════════

NPC_PROMPT = """请为名为 "{world_name}" 的世界生成 {count} 个 NPC 的完整 YAML 配置。
NPC 应涵盖不同社会角色，且有鲜明性格和可交互性。

输出格式（YAML 序列，每项是一个完整的 actor）：
# actor_{i}
id: "{prefix}_{i}"
name: "角色名"
role: "职业"
race: "种族"
personality: "详细性格描述，至少2句话，有矛盾点"
attributes:
  str: 8-18
  dex: 8-18
  con: 8-18
  int: 8-18
  wis: 8-18
  cha: 8-18
combat:
  hp: 10-40
  ac: 10-16
  attack_bonus: 1-6
  damage_dice: 1d4~1d10
functions:
  - "dialogue"
  - "merchant"（可选，看角色）
function_data:
  dialogue:
    topics: ["话题1", "话题2"]
  merchant:（如果是商人）
    gold: 100-500
    shop_inventory: [{item_id: "物品id", qty: 1-5}]

世界主题: {theme}
当前已有 NPC: {existing_npcs}"""

# ═══════════════════════════════════════════════════════════════
# Scene description prompt
# ═══════════════════════════════════════════════════════════════

SCENE_PROMPT = """请为名为 "{world_name}" 的世界生成 {count} 个场景的详细 YAML 配置。

输出格式（YAML 序列）：
# scene_{i}
id: "{prefix}_{i}"
name: "场景名"
type: "indoor/outdoor/dungeon/urban/wilderness"
description: >
  详细的场景描写，至少5句话。描述环境氛围、视觉细节、气味声音、历史痕迹。 \
  让场景有"可探索感"。
exits:
  - target_scene: "相邻场景id"
    condition: "通过条件"
landmarks:
  - id: "地标id"
    name: "地标名"
    desc: "地标描述"

世界主题: {theme}
已有场景: {existing_scenes}"""

# ═══════════════════════════════════════════════════════════════
# PC prompt
# ═══════════════════════════════════════════════════════════════

PC_PROMPT = """请为名为 "{world_name}" 的世界生成 {count} 个主角团成员的完整 YAML 配置。
主角团应各有特色、互补配合，性格有成长空间。

输出格式（YAML 序列）：
# pc_{i}
id: "{prefix}_{i}"
name: "角色名"
role: "职业/定位"
race: "种族"
personality: "详细性格描述，至少2句话"
attributes:
  str: 10-18
  dex: 10-18
  con: 10-18
  int: 10-18
  wis: 10-18
  cha: 10-18
combat:
  hp: 18-30
  max_hp: 同 hp
  ac: 12-16
  attack_bonus: 3-7
  damage_dice: 1d6~1d10
  damage_bonus: 1-4
equipment:
  weapon: "武器id"
  armor: "防具id"
character_arc:
  goal: "角色长远目标"
  flaw: "角色缺陷"
  turning_point: "可能的人生转折点"

世界主题: {theme}
已有角色: {existing_pcs}"""

# ═══════════════════════════════════════════════════════════════
# Item prompt
# ═══════════════════════════════════════════════════════════════

ITEM_PROMPT = """请为名为 "{world_name}" 的世界生成 {count} 件物品的 YAML 配置。
物品应包含武器、护甲、药水、钥匙等不同类别。

输出格式（YAML 序列）：
# item_{i}
id: "{prefix}_{i}"
name: "物品名"
item_type: "weapon/armor/potion/scroll/key/consumable/misc"
rarity: "common/uncommon/rare/legendary"
weight: 0.1-10.0
value: 5-500
description: "物品描述，1-2句话"
data:
  damage_dice: "1d6"（如果是武器）
  ac_bonus: 2（如果是护甲）
  effect: "效果描述"
  amount: "3d6"（如果是药水）

世界主题: {theme}
已有物品: {existing_items}"""
