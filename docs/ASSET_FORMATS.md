# Formatos de Assets Suportados

## Formatos 3D (modelos e cenas)

| Extensão | Nome | Blender | Godot | Notas |
|---|---|---|---|---|
| `.glb` | glTF binário | ✅ nativo | ✅ nativo | Preferido: inclui texturas embebidas |
| `.gltf` | glTF JSON | ✅ nativo | ✅ nativo | Requer ficheiros `.bin` separados |
| `.bin` | Dados glTF | (dependência) | (dependência) | Sempre acompanha `.gltf` |
| `.blend` | Blender nativo | ✅ nativo | ❌ (exportar primeiro) | Preferido para edição |
| `.fbx` | Autodesk FBX | ✅ (importador) | ✅ (via importador) | Bom para rigging/animação |
| `.obj` | Wavefront OBJ | ✅ nativo | ✅ nativo | Sem animação |
| `.mtl` | Material OBJ | (dependência) | (dependência) | Acompanha `.obj` |
| `.vrm` | VRM (avatar) | ✅ (addon VRM) | ❌ nativo | 100Avatars usa este formato |
| `.stl` | Stereolithography | ✅ nativo | ❌ | Impressão 3D; sem materiais |
| `.dae` | COLLADA | ✅ nativo | ✅ nativo | Formato antigo; evitar para novos assets |
| `.usd` / `.usda` / `.usdc` | Universal Scene Description | ✅ (Blender 4.x+) | ❌ nativo | Suporte experimental |
| `.usdz` | USD comprimido | ✅ (Blender 4.x+) | ❌ nativo | iOS/macOS; suporte limitado |
| `.abc` | Alembic | ✅ (addon) | ❌ | Simulações e VFX |
| `.ply` | Polygon File Format | ✅ nativo | ❌ | Point clouds; uso limitado |

## Formatos de textura e imagem

| Extensão | Nome | Uso |
|---|---|---|
| `.png` | PNG | Texturas com transparência; previews |
| `.jpg` / `.jpeg` | JPEG | Texturas sem transparência; HDRIs low-quality |
| `.tif` / `.tiff` | TIFF | Texturas de alta qualidade |
| `.exr` | OpenEXR | HDR, depth maps, mapas normais high-precision |
| `.hdr` | Radiance HDR | Mapas de ambiente (HDRI) |
| `.webp` | WebP | Texturas web; suporte Blender 3.5+ |
| `.tga` | Targa | Texturas legacy |
| `.bmp` | Bitmap | Texturas legacy |

## Formatos produzidos pelo pipeline Blender

O workflow `blender.yml` pode produzir:

| Formato | Garantido | Condição |
|---|---|---|
| `.blend` | ✅ sempre | `outputs.blend: true` (padrão) |
| `.glb` | ✅ Blender 2.8+ | `outputs.glb: true` |
| `.obj` | ⚠️ depende da versão | `outputs.obj: true` |
| `.fbx` | ⚠️ depende da versão | `outputs.fbx: true` |
| `preview.png` | ✅ sempre | `outputs.preview: true` |

## Importação para Godot

### Formatos recomendados para Godot

1. **GLB** — importação direta, texturas embebidas, sem dependências externas
2. **GLTF** — funciona, mas requer os ficheiros `.bin` e texturas na mesma pasta
3. **OBJ** — apenas geometria e materiais básicos (sem animação)

### Fluxo de exportação Blender → Godot

```
[Blender .blend]
    ↓ export_model (GLB)
[model.glb]
    ↓ copiar para projeto Godot
[res://assets/models/model.glb]
    ↓ Godot importa automaticamente
[MeshInstance3D ou importGLTF]
```

### Notas de compatibilidade

- **VRM** → Godot: não importa nativamente. Converter para GLB no Blender com addon VRM antes de exportar.
- **FBX** → Godot: funciona via importador, mas GLB é preferido.
- **Animações KayKit GLB**: as animações estão embebidas no GLB → importam automaticamente no Godot como AnimationPlayer.
- **Materiais PBR (ambientCG)**: texturas PNG importam diretamente; configurar StandardMaterial3D ou ORM material no Godot.

## Organização de assets importados no Godot

```
res://
├── assets/
│   ├── characters/    ← GLB de personagens (KayKit, 100Avatars convertidos)
│   ├── environments/  ← tilesets GLB (KayKit City, Dungeon)
│   ├── vehicles/      ← GLB de veículos
│   └── materials/     ← texturas PBR (PNG, EXR)
└── scenes/
    └── city_test.tscn ← cena de teste com os assets acima
```
