# Assets 资源清单

> AIGameWorld-Studio 游戏素材库。按场景类型分类，标注来源与许可证。

## 目录结构

```
templates/assets/
├── character_base.png      已有 - 角色基础模板 (32×32)
│
├── dungeon/                地下城
│   ├── Basement.png        来源: SkyOffice (MIT)     512×1600  16×50 tiles
│   ├── dungeon_32x32.png   来源: OpenGameArt CC0     256×256   8×8 tiles
│   └── tiny_dungeon/       来源: Kenney CC0          130+ tiles 16×16
│
├── indoor/                 室内
│   ├── Modern_Office_Black_Shadow.png  来源: SkyOffice (MIT)   512×1696  16×53 tiles
│   └── Classroom_and_library.png      来源: SkyOffice (MIT)   512×1088  16×34 tiles
│
├── outdoor/                野外
│   ├── tuxemon-sample-32px-extruded.png  来源: phaser-rpg 示例     816×1020
│   ├── terrain.png                       来源: LPC (CC-BY-SA)     512×928   16×29 tiles
│   └── terrain.tsx                       来源: LPC                 9 种地形自动过渡
│
├── village/                村庄/城镇
│   ├── Generic.png                      来源: SkyOffice (MIT)     512×2496  16×78 tiles
│   ├── fantasy-tiles.png                来源: phaser examples     512×512
│   └── kenney_rpg_urban/                来源: Kenney CC0          480+ tiles 16×16
│
├── characters/             角色
│   └── lpc_generator/                   来源: LPC (CC-BY-SA)      网页角色生成器
│
└── world_packs/            完整世界包（待填充 .tmx + .tsx）
```

### maps/ 目录（PNG + TMX/JSON 直接可用）

```
templates/assets/maps/
├── roguelike_pack/                    来源: Kenney CC0          16×16
│   ├── Map/sample_indoor.tmx          室内示例地图（100×100）
│   ├── Map/sample_map.tmx             野外示例地图（100×100）
│   └── Spritesheet/roguelikeSheet_transparent.png  968×526
│
├── tuxemon/                           来源: Tuxemon GPL/CC-BY   16×16
│   ├── maps/*.tmx                     263 张完整地图（城镇/路线/洞穴/室内）
│   └── gfx/tilesets/*.tsx + *.png     93 个 tileset 定义 + 图片
│
├── jmb_gameart2d_desert/              来源: gameart2d 免费参考  128×128
├── jmb_jb32/                          来源: jamesbowman 免费    16×16
├── jmb_level25/                       来源: jamesbowman 免费    16×16
├── jmb_magicland/                     来源: jamesbowman 免费    16×16
├── phaser_*/                          来源: Phaser 官方示例     多种
├── puny_dungeon_map1/                 来源: puny_dungeon        32×32
├── SkyOffice_office/                  来源: SkyOffice (MIT)     32×32
└── tuxemon-town/                      来源: phaser-rpg 示例     32×32
```

## 资源来源

| 来源 | 许可证 | 特点 |
|------|--------|------|
| **SkyOffice** | MIT | 开源项目，32×32 像素，4 套室内外 tileset |
| **Kenney** | CC0 | 免费商用，风格统一，含 Tiled 示例文件 |
| **LPC** (Liberated Pixel Cup) | CC-BY-SA 3.0/4.0 | 32×32，9 种地形过渡，角色生成器 |
| **phaser-rpg** | 示例项目 | 32×32，AIGameWorld 前端默认 tileset |
| **OpenGameArt** | CC0 | 社区免费资源 |
| **phaser examples** | 官方示例 | 附带多种 tileset 示例 |
| **Kenney Roguelike pack** | CC0 | 16×16，2 张示例地图（室内+野外），spritesheet 968×526 |
| **Tuxemon** | GPL-3.0/CC-BY | 16×16，263 张完整地图（城镇/路线/洞穴/室内），93 个 tileset |

## 场景覆盖

| 场景类型 | 可用资源 | 质量 |
|---------|---------|------|
| dungeon | Basement + dungeon_32x32 + Tiny Dungeon + Roguelike pack | ⭐⭐⭐⭐ |
| indoor | Modern_Office + Classroom_and_library + Tuxemon 室内地图 | ⭐⭐⭐ |
| outdoor | tuxemon + LPC terrain (.tsx) + Tuxemon 路线地图 | ⭐⭐⭐⭐ |
| village | Generic + fantasy-tiles + Kenney RPG Urban + Tuxemon 城镇地图 | ⭐⭐⭐ |

## 待获取

- [x] Kenney Roguelike/RPG pack（CC0，2 张 .tmx 示例地图）
- [x] Tuxemon 完整地图集（263 张 .tmx + 93 个 tileset，GPL/CC-BY）
- [ ] Kenney All-in-1 完整包（40,000+ assets，含 UI/音频）
- [ ] itch.io 完整 .tmx 世界包（带 collision/object layer）
- [ ] DawnLike tileset (CC-BY-SA 3.0, GitHub)
- [ ] LPC NPC 精灵图（离线版）
- [ ] Kenney UI Pack（按钮/面板/滑条）

## 注意事项

- Kenney RPG Urban 和 Tiny Dungeon 是 **16×16** 像素，需 2x 缩放才能在 32px 网格下使用
- LPC terrain `.tsx` 支持 Tiled 地形刷自动过渡，是户外地图生成的核心
- SkyOffice tileset 都是 **512px 宽 × N px 高**的 spritesheet，按 32×32 切分
- 完整 .tmx 世界包是最高优先级待获取项——包含图层/碰撞/对象结构，AI 可直接解析
