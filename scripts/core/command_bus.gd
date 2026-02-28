extends Node
class_name CommandBus

var _queue: Array[Dictionary] = []

func enqueue(command: Dictionary) -> void:
	if not command.has("type"):
		push_warning("Command rejected: missing 'type' field.")
		return
	_queue.append(command.duplicate(true))

func drain() -> Array[Dictionary]:
	var drained: Array[Dictionary] = _queue.duplicate(true)
	_queue.clear()
	return drained
