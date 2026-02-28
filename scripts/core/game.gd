extends Node
class_name Game

var _active_match_path: String = ""

func start_new_match() -> void:
	DataDB.load_all()
	Sim.reset()
	_active_match_path = ""

func load_match(path: String) -> void:
	_active_match_path = path
	# TODO: Implement match deserialization.

func save_match(path: String) -> void:
	_active_match_path = path
	# TODO: Implement match serialization.
