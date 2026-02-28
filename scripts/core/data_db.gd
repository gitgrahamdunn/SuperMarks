extends Node
class_name DataDB

var units: Dictionary = {}
var buildings: Dictionary = {}
var tech: Dictionary = {}

func load_all() -> void:
	units.clear()
	buildings.clear()
	tech.clear()
	_scan_directory("res://data")
	print("DataDB loaded - units: %d, buildings: %d, tech: %d" % [units.size(), buildings.size(), tech.size()])

func get_unit_def(id: StringName) -> UnitDef:
	return units.get(id)

func get_building_def(id: StringName) -> BuildingDef:
	return buildings.get(id)

func get_tech_def(id: StringName) -> TechDef:
	return tech.get(id)

func _scan_directory(path: String) -> void:
	var dir := DirAccess.open(path)
	if dir == null:
		return

	dir.list_dir_begin()
	var entry := dir.get_next()
	while entry != "":
		if entry.begins_with("."):
			entry = dir.get_next()
			continue
		var full_path := path.path_join(entry)
		if dir.current_is_dir():
			_scan_directory(full_path)
		elif entry.ends_with(".tres"):
			_register_resource(full_path)
		entry = dir.get_next()
	dir.list_dir_end()

func _register_resource(path: String) -> void:
	var resource := load(path)
	if resource is UnitDef:
		var unit := resource as UnitDef
		if unit.id != StringName():
			units[unit.id] = unit
	elif resource is BuildingDef:
		var building := resource as BuildingDef
		if building.id != StringName():
			buildings[building.id] = building
	elif resource is TechDef:
		var tech_def := resource as TechDef
		if tech_def.id != StringName():
			tech[tech_def.id] = tech_def
