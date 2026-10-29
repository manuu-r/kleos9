typedef JsonMap = Map<String, dynamic>;

JsonMap asMap(Object? value) => value is Map<Object?, Object?>
    ? Map<String, dynamic>.from(value)
    : <String, dynamic>{};
List<JsonMap> asMaps(Object? value) => asList(value)
    .whereType<Map<Object?, Object?>>()
    .map(asMap)
    .toList(growable: false);
List<dynamic> asList(Object? value) =>
    value is List<dynamic> ? value : const <dynamic>[];
Object? immutableJson(Object? value) {
  if (value is Map<Object?, Object?>) {
    return Map<String, dynamic>.unmodifiable(value
        .map((key, value) => MapEntry(key.toString(), immutableJson(value))));
  }
  if (value is List<dynamic>) {
    return List<dynamic>.unmodifiable(value.map(immutableJson));
  }
  return value;
}

/// Returns [next] with every subtree that is deep-equal to [previous] replaced
/// by the previous object. Unchanged data keeps its identity across polls, so
/// `select` on a slice of state only fires when that slice really changed.
Object? shareStructure(Object? previous, Object? next) {
  if (identical(previous, next)) return next;
  if (previous is Map<String, dynamic> && next is Map<String, dynamic>) {
    var same = previous.length == next.length;
    final merged = <String, dynamic>{};
    for (final entry in next.entries) {
      final value = shareStructure(previous[entry.key], entry.value);
      merged[entry.key] = value;
      if (same &&
          (!previous.containsKey(entry.key) ||
              !identical(previous[entry.key], value))) {
        same = false;
      }
    }
    return same ? previous : Map<String, dynamic>.unmodifiable(merged);
  }
  if (previous is List<dynamic> && next is List<dynamic>) {
    var same = previous.length == next.length;
    final merged = <dynamic>[];
    for (var i = 0; i < next.length; i++) {
      final value =
          shareStructure(i < previous.length ? previous[i] : null, next[i]);
      merged.add(value);
      if (same && !identical(previous[i], value)) same = false;
    }
    return same ? previous : List<dynamic>.unmodifiable(merged);
  }
  return previous == next ? previous : next;
}
