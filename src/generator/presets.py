# 内置模板数据 / Preset Template Data
# forgotten_realm 预设——2 PC + 2 Actor + 2 场景 + 7 物品 + 剧情
# 可用做默认世界示例和测试基准 / Default world example and test baseline

PRESETS = {
    "forgotten_realm": {
        "meta": {
            # 世界基本元信息 / World metadata
            "id": "forgotten_realm",
            "name": "遗忘国度",
            "ruleset": "d20",
            "starting_scene": "tavern",
            "description": "一个充满冒险与传说的中世纪奇幻世界",
        },
        # 世界观设定 / World lore
        "lore": [
            {
                "id": "forest_history",
                "category": "history",
                "content": "古老的森林曾经是精灵王国的一部分，王国陨落后，森林深处仍有古老魔法的痕迹。",
                "references": ["forest"],
            },
            {
                "id": "dwarf_culture",
                "category": "culture",
                "content": "矮人以锻造闻名，每一个学徒要花十年学习打造技艺，他们的武器在市场上十分抢手。",
                "references": ["blacksmith"],
            },
        ],
        # 场景 / Scenes
        "scenes": [
            {
                "id": "tavern",
                "name": "酒馆",
                "type": "indoor",
                "description": "温暖喧闹的酒馆，炉火烧得正旺，角落里有吟游诗人在弹琴。",
                "exits": [{"target_scene": "forest", "condition": ""}],
                "landmarks": [{"id": "bar", "name": "吧台", "desc": "旅店老板在此招待客人"}],
            },
            {
                "id": "forest",
                "name": "森林",
                "type": "wilderness",
                "description": "黑暗古老的森林，树冠遮天蔽日，地面铺满腐叶。",
                "exits": [{"target_scene": "tavern", "condition": ""}],
                "landmarks": [{"id": "ancient_oak", "name": "古橡树", "desc": "树干上刻着古老的符文"}],
            },
        ],
        # 玩家角色 / Player characters
        "player_characters": [
            {
                "id": "warrior",
                "name": "战士",
                "role": "warrior",
                "race": "human",
                "personality": "勇敢、正直、有时鲁莽",
                "attributes": {"str": 16, "dex": 12, "con": 16, "int": 10, "wis": 12, "cha": 10},
                "combat": {"hp": 30, "ac": 16, "attack_bonus": 5, "damage_bonus": 3},
                "equipment": {"weapon": "longsword", "armor": "chain_mail"},
                "character_arc": {"goal": "成为传奇战士", "flaw": "过度自信", "turning_point": "首败"},
            },
            {
                "id": "mage",
                "name": "法师",
                "role": "mage",
                "race": "elf",
                "personality": "聪明、谨慎、充满好奇心",
                "attributes": {"str": 8, "dex": 14, "con": 12, "int": 17, "wis": 14, "cha": 12},
                "combat": {"hp": 18, "ac": 12, "attack_bonus": 3, "damage_bonus": 1},
                "equipment": {"weapon": "staff", "armor": "robe"},
                "character_arc": {"goal": "解开古老魔法奥秘", "flaw": "对未知的痴迷", "turning_point": "失控的实验"},
            },
        ],
        # NPC 角色 / Actors
        "actors": [
            {
                "id": "blacksmith",
                "name": "铁匠",
                "role": "blacksmith",
                "race": "dwarf",
                "personality": "勤奋、沉默寡言、对陌生人警惕",
                "attributes": {"str": 16, "dex": 10, "con": 16, "int": 10, "wis": 12, "cha": 8},
                "combat": {"hp": 30, "ac": 15, "attack_bonus": 5, "damage_bonus": 3},
                "functions": ["dialogue", "merchant"],
                "function_data": {
                    "merchant": {
                        "gold": 500,
                        "shop_inventory": [{"item_id": "longsword", "qty": 2}, {"item_id": "shield", "qty": 1}],
                    }
                },
                "equipment": {"weapon": "war_hammer"},
            },
            {
                "id": "innkeeper",
                "name": "旅店老板",
                "role": "innkeeper",
                "race": "human",
                "personality": "热情好客、消息灵通",
                "attributes": {"str": 10, "dex": 12, "con": 10, "int": 12, "wis": 14, "cha": 16},
                "functions": ["dialogue", "quest_giver"],
                "function_data": {"dialogue": {"topics": ["rumors", "quests", "local_info"]}},
            },
        ],
        # 物品 / Items
        "items": [
            {
                "id": "longsword",
                "name": "长剑",
                "item_type": "weapon",
                "rarity": "common",
                "weight": 3.0,
                "value": 15,
                "data": {"damage_dice": "1d8", "damage_type": "slashing"},
            },
            {
                "id": "shield",
                "name": "盾牌",
                "item_type": "armor",
                "rarity": "common",
                "weight": 6.0,
                "value": 10,
                "data": {"ac_bonus": 2},
            },
            {
                "id": "war_hammer",
                "name": "战锤",
                "item_type": "weapon",
                "rarity": "common",
                "weight": 5.0,
                "value": 20,
                "data": {"damage_dice": "1d10", "damage_type": "bludgeoning"},
            },
            {
                "id": "staff",
                "name": "法杖",
                "item_type": "weapon",
                "rarity": "common",
                "weight": 4.0,
                "value": 5,
                "data": {"damage_dice": "1d6", "damage_type": "bludgeoning"},
            },
            {
                "id": "chain_mail",
                "name": "锁子甲",
                "item_type": "armor",
                "rarity": "uncommon",
                "weight": 25.0,
                "value": 75,
                "data": {"ac_bonus": 5},
            },
            {
                "id": "healing_potion",
                "name": "治疗药水",
                "item_type": "potion",
                "rarity": "common",
                "weight": 0.5,
                "value": 50,
                "data": {"effect": "heal", "amount": "2d4+2"},
            },
            {
                "id": "robe",
                "name": "法师长袍",
                "item_type": "armor",
                "rarity": "common",
                "weight": 2.0,
                "value": 5,
                "data": {"ac_bonus": 1},
            },
        ],
        # 场景内交互对象 / Scene interactive objects
        "scene_objects": [
            {
                "id": "tavern_chest",
                "name": "酒馆宝箱",
                "object_type": "chest",
                "scene_id": "tavern",
                "interact_data": {"locked": False, "dc": 10, "loot_table": [{"item_id": "healing_potion", "qty": 1}]},
            },
            {
                "id": "forest_trap",
                "name": "森林陷阱",
                "object_type": "trap",
                "scene_id": "forest",
                "interact_data": {"dc": 13, "damage": "1d6"},
            },
        ],
        # 剧情设置 / Story setup
        "story_setup": {
            "arcs": [
                {
                    "type": "main",
                    "title": "失踪的商队",
                    "stage": "hook",
                    "main_cast": ["warrior", "mage"],
                    "branching_points": [{"trigger": "追踪痕迹", "next": "森林遭遇"}],
                }
            ],
            "hooks": [
                {"description": "旅店老板提到最近森林里有奇怪的声音", "urgency": "medium"},
                {"description": "铁匠收到了一个神秘的订单", "urgency": "low"},
            ],
        },
    }
}
