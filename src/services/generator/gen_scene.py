"""从 Tuxemon TMX 生成 AIGameWorld scene 记录并写入 DB / Generate + write to DB."""

import json
import sqlite3
import sys
from pathlib import Path

import pytiled_parser
from pytiled_parser.layer import ObjectLayer, TileLayer

# 定位 Studio 项目根：gen_scene.py 位于 <root>/src/services/generator/，向上 4 层到根。
# 用锚定 src 包的方式更稳健，避免层数硬编码随目录结构调整而失效。
_HERE = Path(__file__).resolve()
PROJECT_ROOT = _HERE.parent.parent.parent.parent  # .../AIGameWorld-Studio
# AIGameWorld 是与 Studio 平级的兄弟项目；素材/DB 应写入 AIGameWorld，而非 Studio 内部。
AIGAMEWORLD_ROOT = PROJECT_ROOT.parent / "AIGameWorld"
ASSETS = AIGAMEWORLD_ROOT / "frontend" / "public" / "assets"
AIGAME_DB = AIGAMEWORLD_ROOT / "backend" / "data" / "dev.db"

TUX_TSX_DIR = "assets/tuxemon/gfx/tilesets"
TUX_PNG_DIR = "assets/tuxemon/gfx/tilesets"
TUX_MAP_DIR = "assets/tuxemon/maps"


def gen_scene(tmx_path: str):
    """解析 TMX，生成完整 scene 记录、ext_json、tilemap_summary、Phaser JSON."""
    tmx = Path(tmx_path)
    scene_id = tmx.stem
    name = scene_id.replace("_", " ").title()

    # ── 一次解析，全部数据来源 / Single parse, single source of truth ──
    m = pytiled_parser.parse_map(tmx)
    w, h = m.map_size.width, m.map_size.height
    tw, th = m.tile_size.width, m.tile_size.height

    # ── 地图属性 / Map properties ──
    props = dict(m.properties) if m.properties else {}
    map_type = props.get("map_type", "outdoor")
    inside = _as_bool(props.get("inside", False))
    clamped = props.get("edges") == "clamped"
    scenario = props.get("scenario", "")
    cardinal = {d: props[d] for d in ("north", "south", "east", "west") if d in props}
    endure = props.get("endure", "")
    enter_from = props.get("enter_from", "")
    exit_from = props.get("exit_from", "")
    key = props.get("key", "")

    if inside:
        map_type = "indoor"

    # ── tilesets：PNG + TSX 路径 / Tilesets: PNG URLs + TSX paths ──
    tilesets: list[dict] = []
    ts_names: list[str] = []
    tsx_paths: list[str] = []

    for ts_key, ts in m.tilesets.items():
        ts_name = ts.name or str(ts_key)
        ts_names.append(ts_name)

        # PNG URL
        try:
            img_rel = ts.image.resolve().relative_to(ASSETS.resolve())
            png_url = f"assets/{img_rel.as_posix()}"
        except ValueError:
            png_url = f"{TUX_PNG_DIR}/{ts.image.name}"

        tilesets.append({"name": ts_name, "url": png_url})

        # TSX 路径（Tuxemon 约定：gfx/tilesets/{name}.tsx）
        tsx_paths.append(f"{TUX_TSX_DIR}/{ts_name}.tsx")

    # ── TSX 语义：tile 级别属性（surfable/endure）/ Tile-level semantics ──
    has_surfable = False
    has_endure = False
    for _tsk, ts_obj in m.tilesets.items():
        if ts_obj.tiles:
            for td in ts_obj.tiles.values():
                if td.properties:
                    td_props = dict(td.properties)
                    if "surfable" in td_props:
                        has_surfable = True
                    if "endure" in td_props:
                        has_endure = True
                if has_surfable and has_endure:
                    break

    # ── 图层 / Layers ──
    tile_layers = [ly for ly in m.layers if isinstance(ly, TileLayer)]
    object_layers = [ly for ly in m.layers if isinstance(ly, ObjectLayer)]
    layer_names = [ly.name for ly in tile_layers]

    # ── 出口 + NPC + 出生点 + 可交互物 / Exits + NPCs + Spawn + Interactables ──
    exits: list[dict] = []
    npcs: list[dict] = []
    collision_rects: list[dict] = []
    interactables: list[dict] = []
    encounter_zones: list[dict] = []
    spawns: list[dict] = []
    shops: list[dict] = []
    start_battles: list[dict] = []
    spawn_x = spawn_y = 0

    for ol in object_layers:
        for obj in ol.tiled_objects:
            obj_props = dict(obj.properties) if obj.properties else {}
            obj_x = int(obj.coordinates.x) // tw if obj.coordinates else 0
            obj_y = int(obj.coordinates.y) // th if obj.coordinates else 0
            act10 = obj_props.get("act10", "")
            obj_name = obj.name or ""

            # 传送出口
            if "transition_teleport" in act10:
                parts = act10.replace("transition_teleport player,", "").split(",")
                exits.append(
                    {
                        "target": parts[0].strip() if parts else "",
                        "position": {"x": obj_x, "y": obj_y},
                        "name": obj_name,
                    }
                )

            # 出生点
            if not spawn_x and "char_at player" in obj_props.get("cond10", ""):
                spawn_x, spawn_y = obj_x, obj_y

            # NPC 碰撞体（必须在告示牌之前处理，用于去重）
            if ol.name.lower().startswith("collision") and obj_name:
                npcs.append({"name": obj_name, "x": obj_x, "y": obj_y})
            # 无名碰撞矩形（墙壁/障碍物）
            elif ol.name.lower().startswith("collision"):
                obj_w = int(obj.size.width) // tw if obj.size else 0
                obj_h = int(obj.size.height) // th if obj.size else 0
                if obj_w > 0 and obj_h > 0:
                    collision_rects.append({"x": obj_x, "y": obj_y, "w": obj_w, "h": obj_h})

            # 告示牌/可交互物（events 层，translated_dialog 非传送，且非 NPC 已有名）
            if "translated_dialog" in act10 and "transition_teleport" not in act10:
                dialog_id = act10.replace("translated_dialog ", "").strip()
                # 跳过 collision 层已有的 NPC 名
                if obj_name and not any(n["name"] == obj_name for n in npcs):
                    interactables.append(
                        {
                            "name": obj_name,
                            "dialog_id": dialog_id,
                            "position": {"x": obj_x, "y": obj_y},
                        }
                    )
                elif not obj_name:
                    interactables.append(
                        {
                            "name": "",
                            "dialog_id": dialog_id,
                            "position": {"x": obj_x, "y": obj_y},
                        }
                    )

            # 战斗遭遇区
            if "random_encounter" in act10:
                enc_type = act10.replace("random_encounter ", "").strip()
                encounter_zones.append(
                    {
                        "name": obj_name,
                        "encounter_type": enc_type,
                        "position": {"x": obj_x, "y": obj_y},
                    }
                )

            # NPC 创建点（解析 create_npc name,x,y[,behavior]）
            if "create_npc" in act10:
                raw_params = act10.replace("create_npc ", "").strip()
                parts = raw_params.split(",")
                npc_type = parts[0].strip() if parts else raw_params
                sx = int(parts[1]) if len(parts) > 1 else obj_x
                sy = int(parts[2]) if len(parts) > 2 else obj_y
                behavior = parts[3].strip() if len(parts) > 3 else "stand"
                spawns.append(
                    {
                        "name": obj_name or npc_type,
                        "npc_type": npc_type,
                        "spawn_position": {"x": sx, "y": sy},
                        "behavior": behavior,
                    }
                )

            # Boss 战斗
            if "start_battle" in act10 and "transition_teleport" not in act10:
                raw = act10.replace("start_battle ", "").strip()
                start_battles.append(
                    {
                        "name": obj_name or raw,
                        "battle_id": raw,
                        "position": {"x": obj_x, "y": obj_y},
                    }
                )

            # 商店
            if "open_shop" in act10:
                shops.append(
                    {
                        "name": obj_name,
                        "position": {"x": obj_x, "y": obj_y},
                    }
                )

    # ── 区域标注 / Zone labelling (仅出口+战斗区，不含交互物) ──
    zones: list[dict] = []
    for e in exits:
        zones.append(
            {
                "name": e["name"] or e["target"].replace(".tmx", ""),
                "position": e["position"],
            }
        )
    for ez in encounter_zones:
        zones.append(
            {
                "name": ez["name"] or ez["encounter_type"],
                "position": ez["position"],
            }
        )
    for bb in start_battles:
        zones.append(
            {
                "name": bb["name"],
                "position": bb["position"],
            }
        )

    if not spawn_x:
        spawn_x, spawn_y = w // 2, h // 2

    # ── 地形推断 / Terrain inference ──
    has_water = any(k in t for t in ts_names for k in ("water", "ocean"))
    has_nature = any(k in t for t in ts_names for k in ("nature", "grass"))
    has_building = any("building" in t for t in ts_names)
    has_cave = any(k in t for t in ts_names for k in ("cave", "dungeon"))
    has_ice = any(k in t for t in ts_names for k in ("ice", "snow"))

    # ── 构建摘要 JSON / Build summary JSON ──
    summary = {
        "id": scene_id,
        "name": name,
        "type": map_type,
        "inside": inside,
        "edges": "clamped" if clamped else "",
        "scenario": scenario,
        "size": f"{w}×{h}（{w * tw}×{h * tw} 像素）",
        "tile_size": tw,
        "tilesets": ts_names,
        "layers": layer_names,
        "terrain": {
            "water": has_water,
            "nature": has_nature,
            "building": has_building,
            "cave": has_cave,
            "ice_snow": has_ice,
            "surfable": has_surfable,
            "endure_terrain": has_endure,
        },
        "cardinal_directions": cardinal,
        "exits": exits,
        "npcs": [n["name"] for n in npcs if n["name"]],
        "interactables": [
            {"name": i["name"] or i["dialog_id"], "dialog_id": i["dialog_id"], "position": i["position"]}
            for i in interactables
            if i["dialog_id"]
        ],
        "encounter_zones": [
            {"name": e["name"] or e["encounter_type"], "type": e["encounter_type"], "position": e["position"]}
            for e in encounter_zones
        ],
        "spawn_points": [
            {
                "name": s["name"] or s["npc_type"],
                "npc_type": s["npc_type"],
                "position": s["spawn_position"],
                "behavior": s["behavior"],
            }
            for s in spawns
        ],
        "shops": [{"name": s["name"] or "shop", "position": s["position"]} for s in shops],
        "start_battles": [
            {"name": b["name"], "battle_id": b["battle_id"], "position": b["position"]} for b in start_battles
        ],
        "zones": [{"name": z["name"], "position": z["position"]} for z in zones],
        "spawn": {"x": spawn_x, "y": spawn_y},
        "endure": endure,
        "enter_from": enter_from,
        "exit_from": exit_from,
        "key_item": key,
        "collision_rects": collision_rects,
    }

    # ── 完整解读 / Full interpretation + description ──
    full, desc = _build_interpretation(
        name,
        map_type,
        inside,
        clamped,
        scenario,
        w,
        h,
        tw,
        ts_names,
        has_water,
        has_nature,
        has_building,
        has_cave,
        has_ice,
        has_surfable,
        has_endure,
        layer_names,
        cardinal,
        exits,
        npcs,
        interactables,
        encounter_zones,
        start_battles,
        shops,
        zones,
        spawn_x,
        spawn_y,
    )
    summary["full_interpretation"] = full
    summary_json = json.dumps(summary, ensure_ascii=False)

    # ── 拷贝资产 + TMX → Phaser JSON ──
    _copy_assets(tmx, tilesets, tsx_paths)  # 先建目录
    _convert_to_json(m, ASSETS / "tuxemon" / "maps" / f"{scene_id}.json")

    # ── ext_json / 前端加载用 ──
    ext = {
        "tile_size": tw,
        "tmx_path": f"{TUX_MAP_DIR}/{scene_id}.tmx",
        "tilemap_url": f"{TUX_MAP_DIR}/{scene_id}.json",
        "tsx_paths": tsx_paths,
        "tilesets": tilesets,
        "collision_rects": collision_rects,
    }

    return {
        "id": scene_id,
        "name": name,
        "type": map_type,
        "description": desc,
        "spawn_x": spawn_x,
        "spawn_y": spawn_y,
        "map_width": w,
        "map_height": h,
        "world_id": "mock_world",
        "tilemap_summary": summary_json,
        "ext_json": json.dumps(ext, ensure_ascii=False),
        # 提取的实体数据，供 upsert_actors / upsert_scene_objects 使用
        "_npcs": npcs,
        "_spawns": spawns,
        "_interactables": interactables,
    }


# ──────────────────────────────────────────────
# 辅助函数 / Helpers
# ──────────────────────────────────────────────


def _as_bool(val) -> bool:
    """Normalize bool/str/int to Python bool."""
    if isinstance(val, bool):
        return val
    return str(val).lower() in ("true", "1")


def _build_interpretation(
    name,
    map_type,
    inside,
    clamped,
    scenario,
    w,
    h,
    tw,
    ts_names,
    has_water,
    has_nature,
    has_building,
    has_cave,
    has_ice,
    has_surfable,
    has_endure,
    layer_names,
    cardinal,
    exits,
    npcs,
    interactables,
    encounter_zones,
    start_battles,
    shops,
    zones,
    spawn_x,
    spawn_y,
):
    """生成中文完整解读，返回 (full_interpretation, description).
    description 不含战役信息和图层信息，适配项目 prompt 上下文.
    """
    type_label = {
        "town": "城镇",
        "route": "野外路线",
        "dungeon": "地下城",
        "indoor": "室内空间",
        "shop": "商店",
        "clinic": "诊所",
        "notype": "特殊区域",
        "outdoor": "室外区域",
    }
    t_label = type_label.get(map_type, map_type)
    quantifier = "一座" if map_type == "town" else "一个"

    # 公共基础 / shared base
    base = [f"{name}，{quantifier}{t_label}。"]
    base.append(f"地图尺寸 {w}×{h} 格（{w * tw}×{h * tw} 像素），使用 16×16 的瓦片规格。")

    # 边界（仅 full）
    clamped_part = "地图边界锁定，玩家无法走出地图范围，这是一个封闭区域。" if clamped else ""

    # 战役（仅 full）
    scenario_part = f"该地图属于「{scenario}」战役。" if scenario else ""

    # 地形
    terrain = []
    if inside:
        terrain.append("这是一个室内场景，四周由墙壁围合，没有自然采光。")
    else:
        tp = []
        if has_water:
            tp.append("可涉水水域" if has_surfable else "水域")
        if has_nature:
            tp.append("树木和岩石等自然地貌")
        if has_building:
            tp.append("建筑群")
        if has_cave:
            tp.append("洞穴")
        if has_ice:
            tp.append("冰雪地形" + ("（可滑行）" if has_surfable else ""))
        if tp:
            terrain.append("地形包含" + "、".join(tp) + "，构成了一座多样化的场景。")
    if has_endure:
        terrain.append("部分区域有特殊地形效果（如崎岖路面）。")

    # tileset 外观
    visual = {
        "core_outdoor": "草地和石砖路面",
        "core_buildings": "房屋墙壁和屋顶",
        "core_indoor_floors": "室内木地板",
        "core_indoor_walls": "室内砖墙",
        "core_indoor_stairs": "楼梯结构",
        "core_outdoor_nature": "树木和灌木",
        "core_outdoor_water": "水面和河岸",
        "core_city_and_country": "栅栏和城镇装饰",
        "core_set pieces": "场景摆件",
        "factory": "工业设施",
        "oceanset_outside": "海滩和浅水",
        "rubberduck_outdoor": "彩色街道路面",
        "cave": "洞穴岩壁",
        "ice": "冰面",
        "snow": "雪地",
    }
    vis_desc = [visual.get(t, t) for t in ts_names if visual.get(t)]
    vis_part = f"地面铺设了{'、'.join(vis_desc)}等瓦片素材。" if vis_desc else ""

    # 方位
    dirs = [f"{d}方向通往「{v}」" for d, v in cardinal.items()]
    cardinal_part = "从地图上看，" + "，".join(dirs) + "。" if cardinal else ""

    # 出口
    exit_parts = []
    for e in exits:
        en = e["name"] or e["target"]
        ep = f"({e['position']['x']},{e['position']['y']})"
        exit_parts.append(f"「{en}」位于{ep}")
    exit_part = f"地图共有 {len(exits)} 个传送出口：" + "；".join(exit_parts) + "。" if exits else ""

    # NPC（带坐标）
    npc_parts = [f"{n['name']}({n['x']},{n['y']})" for n in npcs if n["name"]]
    npc_part = f"可交互的 NPC 包括：{'、'.join(npc_parts)}。" if npc_parts else ""

    # 告示牌/可交互物（带坐标）
    sign_parts = []
    for i in interactables:
        if i["dialog_id"]:
            nm = i["name"] or i["dialog_id"]
            pos = i["position"]
            sign_parts.append(f"{nm}({pos['x']},{pos['y']})")
    sign_part = f"可阅读/交互的物体：{'、'.join(sign_parts)}。" if sign_parts else ""

    # 战斗遭遇区
    zone_names = [e["name"] or e["encounter_type"] for e in encounter_zones]
    zone_part = ""
    if zone_names:
        zone_part = f"战斗遭遇区域：{'、'.join(zone_names)}。"

    # Boss 战斗
    boss_parts = [f"{b['name']}({b['position']['x']},{b['position']['y']})" for b in start_battles]
    boss_part = f"Boss 战斗：{'、'.join(boss_parts)}。" if boss_parts else ""

    # 可探索区域
    zone_entries = [f"{z['name']}({z['position']['x']},{z['position']['y']})" for z in zones]
    zone_text = f"可探索区域：{'、'.join(zone_entries)}。" if zone_entries else ""

    # 商店
    shop_names = [s["name"] or "商店" for s in shops]
    shop_part = f"商店：{'、'.join(shop_names)}。" if shop_names else ""

    # 出生点
    spawn_part = f"玩家出生点位于 ({spawn_x},{spawn_y})。"

    # 图层（仅 full）
    layer_part = ""
    if layer_names:
        layer_part = f"地图使用 {len(layer_names)} 个渲染图层，从下到上依次为：{'、'.join(layer_names)}。"

    # 结语
    if inside:
        tag = "室内"
    elif map_type == "town":
        tag = "城镇"
    elif map_type == "dungeon":
        tag = "地下城"
    else:
        tag = t_label
    ending = f"整体来看，这是一个典型的RPG{tag}地图。"

    # ── 组装 ──
    full = "".join(
        p
        for p in [
            *base,
            clamped_part,
            scenario_part,
            *terrain,
            vis_part,
            cardinal_part,
            exit_part,
            npc_part,
            sign_part,
            zone_text,
            zone_part,
            boss_part,
            shop_part,
            spawn_part,
            layer_part,
            ending,
        ]
        if p
    )
    desc = "".join(
        p
        for p in [
            *base,
            *terrain,
            vis_part,
            cardinal_part,
            npc_part,
            sign_part,
            zone_text,
            zone_part,
            boss_part,
            shop_part,
            spawn_part,
            ending,
        ]
        if p
    )
    return full, desc


def _copy_assets(tmx: Path, tilesets: list[dict], tsx_paths: list[str] = None):
    """拷贝 TMX、TSX 和 tileset PNG 到 AIGameWorld public/assets."""
    import shutil

    # TMX
    tux_maps = ASSETS / "tuxemon" / "maps"
    tux_maps.mkdir(parents=True, exist_ok=True)
    dest_tmx = tux_maps / tmx.name
    if not dest_tmx.exists():
        shutil.copy2(tmx, dest_tmx)

    # TSX
    if tsx_paths:
        tux_tsx = ASSETS / "tuxemon" / "gfx" / "tilesets"
        tux_tsx.mkdir(parents=True, exist_ok=True)
        for tsx_url in tsx_paths:
            tsx_name = tsx_url.split("/")[-1]
            studio_tsx = PROJECT_ROOT / "templates" / "assets" / "maps" / "tuxemon" / "gfx" / "tilesets" / tsx_name
            if studio_tsx.exists():
                dest_tsx = tux_tsx / tsx_name
                if not dest_tsx.exists():
                    shutil.copy2(studio_tsx, dest_tsx)

    # PNG
    for ts in tilesets:
        url = ts["url"]
        if url.startswith("assets/"):
            png_rel = url[len("assets/") :]
            png_path = ASSETS / png_rel
            if not png_path.exists():
                studio_png = PROJECT_ROOT / "templates" / "assets" / "maps" / png_rel
                if studio_png.exists():
                    png_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(studio_png, png_path)


def _convert_to_json(map_data, dest: Path) -> str:
    """Convert pytiled_parser map → Phaser-compatible Tiled JSON file."""
    # ── Tile layers ──
    json_layers = []
    for layer in map_data.layers:
        if isinstance(layer, TileLayer):
            data = []
            for row in layer.data:
                data.extend(row)
            json_layers.append(
                {
                    "id": layer.id,
                    "name": layer.name,
                    "type": "tilelayer",
                    "width": layer.size.width,
                    "height": layer.size.height,
                    "x": 0,
                    "y": 0,
                    "opacity": layer.opacity,
                    "visible": layer.visible,
                    "data": data,
                }
            )

    # ── Object layers ──
    for layer in map_data.layers:
        if isinstance(layer, ObjectLayer):
            objects = []
            for obj in layer.tiled_objects:
                props_list = []
                if obj.properties:
                    for pk, pv in obj.properties.items():
                        props_list.append({"name": pk, "type": "string", "value": str(pv)})
                objects.append(
                    {
                        "id": obj.id,
                        "name": obj.name or "",
                        "type": getattr(obj, "class_", "") or "",
                        "x": obj.coordinates.x if obj.coordinates else 0,
                        "y": obj.coordinates.y if obj.coordinates else 0,
                        "width": obj.size.width if obj.size else 0,
                        "height": obj.size.height if obj.size else 0,
                        "properties": props_list,
                    }
                )
            json_layers.append(
                {
                    "id": layer.id,
                    "name": layer.name,
                    "type": "objectgroup",
                    "opacity": layer.opacity,
                    "visible": layer.visible,
                    "draworder": layer.draw_order or "topdown",
                    "objects": objects,
                }
            )

    # ── Tilesets ──
    json_tilesets = []
    for _ts_key, ts in map_data.tilesets.items():
        json_tilesets.append(
            {
                "firstgid": ts.firstgid,
                "name": ts.name or str(_ts_key),
                "tilewidth": ts.tile_width,
                "tileheight": ts.tile_height,
                "tilecount": ts.tile_count,
                "image": f"../gfx/tilesets/{ts.image.name}",
                "imagewidth": ts.image_width,
                "imageheight": ts.image_height,
            }
        )

    # ── Map properties ──
    map_props = []
    if map_data.properties:
        for pk, pv in map_data.properties.items():
            map_props.append({"name": pk, "type": "string", "value": str(pv)})

    result = {
        "width": map_data.map_size.width,
        "height": map_data.map_size.height,
        "tilewidth": map_data.tile_size.width,
        "tileheight": map_data.tile_size.height,
        "infinite": map_data.infinite,
        "orientation": map_data.orientation,
        "renderorder": map_data.render_order,
        "type": "map",
        "version": "1.10",
        "tiledversion": "1.11.0",
        "properties": map_props,
        "layers": json_layers,
        "tilesets": json_tilesets,
    }

    with open(dest, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)

    return str(dest)


def upsert_scene(scene: dict, db_path: str = None):
    """写入或更新场景记录到 scenes 表."""
    db_path = db_path or str(AIGAME_DB)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT OR REPLACE INTO scenes
          (id, name, type, description, spawn_x, spawn_y,
           map_width, map_height, world_id, tilemap_summary, ext_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
    """,
        (
            scene["id"],
            scene["name"],
            scene["type"],
            scene["description"],
            scene["spawn_x"],
            scene["spawn_y"],
            scene["map_width"],
            scene["map_height"],
            scene["world_id"],
            scene.get("tilemap_summary", ""),
            scene["ext_json"],
        ),
    )
    conn.commit()
    conn.close()
    return scene["id"]


def upsert_actors(scene_id: str, world_id: str, npcs: list[dict], spawns: list[dict], db_path: str = None):
    """从 TMX 解析结果创建场景 NPC 的 Actor 记录 / Create Actor records from parsed NPCs."""
    if not npcs and not spawns:
        return

    db_path = db_path or str(AIGAME_DB)
    conn = sqlite3.connect(db_path)

    entries: list[tuple] = []

    # 碰撞层 NPC / Collision layer NPCs
    for i, npc in enumerate(npcs):
        if not npc.get("name"):
            continue
        entries.append(
            (
                f"{scene_id}_npc_{i + 1}",
                npc["name"],  # name
                "npc",  # role
                None,  # race
                "active",  # status
                "neutral",  # disposition
                scene_id,  # scene_id
                npc["x"],  # position_x
                npc["y"],  # position_y
                "{}",  # attributes_json
                "{}",  # combat_json
                "[]",  # functions_json
                "{}",  # function_data_json
                "[]",  # inventory_json
                "{}",  # relationships_json
                0,  # dm_assigned
                None,  # motivation_injected
                world_id,  # world_id
                '{"source":"tmx"}',  # ext_json
            )
        )

    # create_npc 生成点 / NPC spawn points
    for i, sp in enumerate(spawns):
        name = sp["name"] or sp["npc_type"]
        entries.append(
            (
                f"{scene_id}_spawn_{i + 1}",
                name,
                "npc",
                None,
                "active",
                "neutral",
                scene_id,
                sp["spawn_position"]["x"],
                sp["spawn_position"]["y"],
                "{}",
                "{}",
                "[]",
                "{}",
                "[]",
                "{}",
                0,
                None,
                world_id,
                '{"source":"tmx"}',
            )
        )

    for row in entries:
        conn.execute(
            """INSERT INTO actors (id, name, role, race, status, disposition, scene_id,
            position_x, position_y, attributes_json, combat_json, functions_json,
            function_data_json, inventory_json, relationships_json, dm_assigned,
            motivation_injected, world_id, ext_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
            ON CONFLICT(id) DO UPDATE SET
            status=excluded.status, disposition=excluded.disposition, scene_id=excluded.scene_id,
            position_x=excluded.position_x, position_y=excluded.position_y,
            dm_assigned=excluded.dm_assigned, motivation_injected=excluded.motivation_injected,
            world_id=excluded.world_id, ext_json=excluded.ext_json,
            updated_at=datetime('now', 'localtime')""",
            row,
        )

    conn.commit()
    conn.close()


def upsert_scene_objects(scene_id: str, world_id: str, interactables: list[dict], db_path: str = None):
    """从 TMX 解析结果创建可交互物体记录 / Create SceneObject records from parsed interactables."""
    if not interactables:
        return

    db_path = db_path or str(AIGAME_DB)
    conn = sqlite3.connect(db_path)

    for i, obj in enumerate(interactables):
        obj_name = obj["name"] or obj.get("dialog_id", f"object_{i + 1}")
        interact_data = json.dumps({"dialog_id": obj.get("dialog_id", "")}, ensure_ascii=False)
        conn.execute(
            """INSERT OR REPLACE INTO scene_objects
            (id, name, object_type, scene_id, position_x, position_y,
             interactable, interact_data, world_id, ext_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))""",
            (
                f"{scene_id}_obj_{i + 1}",
                obj_name,
                "decoration",  # object_type — 默认装饰物
                scene_id,
                obj["position"]["x"],
                obj["position"]["y"],
                1,  # interactable = True
                interact_data,
                world_id,
                '{"source":"tmx"}',  # ext_json — 标记 TMX 来源
            ),
        )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        tmx = sys.argv[1]
    else:
        tmx = str(PROJECT_ROOT / "templates" / "assets" / "maps" / "tuxemon" / "maps" / "azure_town.tmx")

    rec = gen_scene(tmx)
    try:
        scid = upsert_scene(rec)
    except Exception:
        scid = rec["id"]
    print(f"[OK] {scid} → {rec['name']} ({rec['type']} {rec['map_width']}×{rec['map_height']})")
    ext = json.loads(rec["ext_json"])
    print(f"  tilesets: {len(ext.get('tilesets', []))}, spawn=({rec['spawn_x']},{rec['spawn_y']})")
    print(f"  完整解读: {rec['description'][:120]}...")

    # 同步写入 NPC Actor + 可交互物体 / Sync actors & scene objects
    upsert_actors(scid, rec["world_id"], rec.get("_npcs", []), rec.get("_spawns", []))
    upsert_scene_objects(scid, rec["world_id"], rec.get("_interactables", []))

    n_npc = len(rec.get("_npcs", []))
    n_spawn = len(rec.get("_spawns", []))
    n_obj = len(rec.get("_interactables", []))
    print(f"  actors: {n_npc} npc + {n_spawn} spawn, objects: {n_obj}")
