extends Resource
class_name BuildingDef

@export var id: StringName
@export var display_name: String = ""
@export var costs: Dictionary = {}
@export var build_time: float = 0.0
@export var hp: int = 0
@export var footprint: Vector2i = Vector2i.ONE
