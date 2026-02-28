# Moon RTS (Godot 4.x)

Moon RTS is a top-down 2D RTS prototype built in Godot 4.x with an authoritative simulation core.

## Requirements

- Godot **4.x** installed (4.2+ recommended).

## Run the project

1. Open Godot 4.x.
2. Import this folder by selecting `project.godot`.
3. Run the project.

Main scene: `res://scenes/game/Main.tscn`

## Architecture rules

- **Authoritative simulation** lives in `Sim` autoload and advances in fixed ticks (`20 TPS`).
- **Commands-only mutation path**: UI and systems submit command dictionaries to `CommandBus`; simulation consumes commands in `Sim.step()`.
- **No gameplay logic in view nodes**: scene scripts display state and trigger high-level calls only.
- **Entity model**: Sim uses integer entity IDs and pure dictionaries (no Node references in sim state).
- **Data-driven stats**: unit/building/tech definitions are `Resource` assets under `res://data/`.
- **Allowed autoloads only**:
  - `Game` -> `res://scripts/core/game.gd`
  - `Sim` -> `res://scripts/core/sim.gd`
  - `CommandBus` -> `res://scripts/core/command_bus.gd`
  - `DataDB` -> `res://scripts/core/data_db.gd`

## Key locations

- Scenes: `res://scenes/`
- Scripts: `res://scripts/`
- Data definitions: `res://data/`

## Smoke harness behavior

At startup (`Main` scene):
- `Game.start_new_match()` resets sim and loads data.
- Spawns a command dome + worker in sim state.
- Sets initial resources.
- HUD shows title, resources, and a tick counter derived from sim tick count.
