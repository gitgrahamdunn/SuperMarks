extends Node2D

@onready var title_label: Label = $UILayer/HUD/TopBar/TitleLabel
@onready var resources_label: Label = $UILayer/HUD/TopBar/ResourcesLabel
@onready var ticks_label: Label = $UILayer/HUD/TopBar/TicksLabel

var _last_reported_second: int = -1

func _ready() -> void:
	title_label.text = "Moon RTS"
	Sim.resources_changed.connect(_on_resources_changed)
	Game.start_new_match()
	_initialize_smoke_match_state()
	_on_resources_changed(Sim.resources)
	_update_tick_label()

func _process(_delta: float) -> void:
	var elapsed_seconds: int = Sim.tick_count / Sim.TICKS_PER_SECOND
	if elapsed_seconds != _last_reported_second:
		_last_reported_second = elapsed_seconds
		_update_tick_label()

func _initialize_smoke_match_state() -> void:
	Sim.spawn_entity(&"command_dome", Vector2(640, 360), 1)
	Sim.spawn_entity(&"worker", Vector2(700, 380), 1)
	Sim.set_resource(&"regolith", 250)
	Sim.set_resource(&"metal", 120)
	Sim.set_resource(&"power", 80)
	Sim.set_resource(&"oxygen", 40)

func _on_resources_changed(new_resources: Dictionary) -> void:
	resources_label.text = "Resources  Regolith: %d  Metal: %d  Power: %d  Oxygen: %d" % [
		int(new_resources.get(&"regolith", 0)),
		int(new_resources.get(&"metal", 0)),
		int(new_resources.get(&"power", 0)),
		int(new_resources.get(&"oxygen", 0)),
	]

func _update_tick_label() -> void:
	ticks_label.text = "Ticks: %d (%.1fs @ %d tps)" % [Sim.tick_count, Sim.tick_count * Sim.DT, Sim.TICKS_PER_SECOND]
