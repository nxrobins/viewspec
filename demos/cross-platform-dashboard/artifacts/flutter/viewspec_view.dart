import 'package:flutter/material.dart';

class ViewSpecActionIntent {
  final String id;
  final String kind;
  final String targetRef;
  final List<String> payloadBindings;
  final Map<String, Object?> payload;

  const ViewSpecActionIntent({
    required this.id,
    required this.kind,
    required this.targetRef,
    required this.payloadBindings,
    required this.payload,
  });
}

typedef ViewSpecActionCallback = void Function(ViewSpecActionIntent intent);

class ViewSpecView extends StatefulWidget {
  final Map<String, Object?> data;
  final ViewSpecActionCallback? onAction;

  const ViewSpecView({super.key, this.data = const <String, Object?>{}, this.onAction});

  @override
  State<ViewSpecView> createState() => _ViewSpecViewState();
}

class _ViewSpecViewState extends State<ViewSpecView> {
  final Map<String, Object?> _inputValues = <String, Object?>{"include_mobile": true, "owner_email": "launch@viewspec.dev", "phase_filter": "Demos"};
  final Map<String, Object?> _compiledPayloadValues = <String, Object?>{"kpi_blockers_value": "1", "launch_status": "On track"};
  final Map<String, Object?> _visibilityRules = <String, Object?>{"show_mobile_note": <String, Object?>{"compareValue": "Mobile", "compareValueType": "string", "fallbackValue": "Demos", "fallbackValueType": "string", "id": "show_mobile_note", "operator": "EQUALS", "sourceKind": "input", "sourceRef": "phase_filter", "targetRef": "binding:mobile_recording_note"}};
  late final TextEditingController _owner_emailController;

  @override
  void initState() {
    super.initState();
    _owner_emailController = TextEditingController(text: '${_inputValues["owner_email"] ?? ""}');
  }

  @override
  void dispose() {
    _owner_emailController.dispose();
    super.dispose();
  }

  Map<String, Object?> _collectPayload(List<String> payloadBindings) {
    final payload = <String, Object?>{};
    for (final bindingId in payloadBindings) {
      if (_inputValues.containsKey(bindingId)) {
        payload[bindingId] = _inputValues[bindingId];
      } else if (_compiledPayloadValues.containsKey(bindingId)) {
        payload[bindingId] = _compiledPayloadValues[bindingId];
      }
    }
    return payload;
  }

  bool _isEmptyRuleValue(Object? value) {
    return value == null || value == '' || (value is List && value.isEmpty);
  }

  String _ruleValueType(Object? value) {
    if (value == null) return 'null';
    if (value is bool) return 'boolean';
    if (value is num) return 'number';
    if (value is String) return 'string';
    if (value is List) return 'array';
    return 'object';
  }

  bool _sameScalar(Object? left, Object? right) {
    final leftType = _ruleValueType(left);
    final rightType = _ruleValueType(right);
    if (!const <String>{'null', 'string', 'number', 'boolean'}.contains(leftType) || leftType != rightType) return false;
    return left == right;
  }

  Object? _ruleSourceValue(Map<String, Object?> rule, Map<String, Object?> inputValues, Map<String, Object?> data) {
    final sourceRef = rule['sourceRef'] as String? ?? '';
    if (rule['sourceKind'] == 'input') {
      return inputValues.containsKey(sourceRef) ? inputValues[sourceRef] : rule['fallbackValue'];
    }
    return data.containsKey(sourceRef) ? data[sourceRef] : rule['fallbackValue'];
  }

  bool _evaluateRule(String ruleId, Map<String, Object?> inputValues, Map<String, Object?> data) {
    final rawRule = _visibilityRules[ruleId];
    if (rawRule is! Map) return true;
    final rule = rawRule.cast<String, Object?>();
    final value = _ruleSourceValue(rule, inputValues, data);
    final operator = rule['operator'];
    if (operator == 'IS_EMPTY') return _isEmptyRuleValue(value);
    if (operator == 'NOT_EMPTY') return !_isEmptyRuleValue(value);
    if (operator == 'IS_TRUE') return value == true;
    if (operator == 'IS_FALSE') return value == false;
    if (operator == 'EQUALS') return _sameScalar(value, rule['compareValue']);
    if (operator == 'NOT_EQUALS') return !_sameScalar(value, rule['compareValue']);
    return true;
  }

  String? _selectValue(String inputId, List<String> options) {
    final value = _inputValues[inputId];
    if (value is String && options.contains(value)) return value;
    if (options.isNotEmpty) return options.first;
    return null;
  }

  void _dispatchAction(String id, String kind, String targetRef, List<String> payloadBindings) {
    widget.onAction?.call(
      ViewSpecActionIntent(
        id: id,
        kind: kind,
        targetRef: targetRef,
        payloadBindings: payloadBindings,
        payload: _collectPayload(payloadBindings),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      key: const ValueKey("dom-region_root"),
      child: SingleChildScrollView(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
              children: <Widget>[
                Wrap(
                  key: const ValueKey("dom-region_header"),
                  spacing: 12,
                  runSpacing: 12,
                  children: <Widget>[
                    Text('${widget.data["launch_title"] ?? "Launch Operations Dashboard"}', key: const ValueKey("dom-binding_launch_title"), style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700, color: const Color(0xFF1F2937))),
                    Container(
                      key: const ValueKey("dom-binding_launch_status"),
                      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                      decoration: BoxDecoration(color: const Color(0xFFF0FDFA), border: Border.all(color: const Color(0xFF99F6E4)), borderRadius: BorderRadius.circular(999)),
                      child: Text('${widget.data["launch_status"] ?? "On track"}', style: TextStyle(fontSize: 14, fontWeight: FontWeight.w700, color: const Color(0xFF0F766E))),
                    ),
                    Text('${widget.data["launch_summary"] ?? "One ViewSpec source proves web, React, iOS, and Android compilation."}', key: const ValueKey("dom-binding_launch_summary"), style: TextStyle(fontSize: 16, fontWeight: FontWeight.w400, color: const Color(0xFF1F2937))),
                  ]
                ),
                GridView.count(
                  key: const ValueKey("dom-region_kpis"),
                  shrinkWrap: true,
                  physics: const NeverScrollableScrollPhysics(),
                  crossAxisCount: 2,
                  crossAxisSpacing: 16,
                  mainAxisSpacing: 16,
                  children: <Widget>[
                    Column(
                      key: const ValueKey("dom-motif_launch_kpis"),
                      mainAxisSize: MainAxisSize.min,
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: <Widget>[
                        Container(
                          key: const ValueKey("dom-motif_launch_kpis_kpi_emitters"),
                          padding: const EdgeInsets.all(16),
                          decoration: BoxDecoration(color: const Color(0xFFFFFFFF), border: Border.all(color: const Color(0xFFE2E8F0)), borderRadius: BorderRadius.circular(12)),
                          child: Column(
                            mainAxisSize: MainAxisSize.min,
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                              children: <Widget>[
                                Text('${widget.data["kpi_emitters_label"] ?? "Emitter targets"}', key: const ValueKey("dom-binding_kpi_emitters_label"), style: TextStyle(fontSize: 12, fontWeight: FontWeight.w800, color: const Color(0xFF64748B))),
                                Text('${widget.data["kpi_emitters_value"] ?? "4"}', key: const ValueKey("dom-binding_kpi_emitters_value"), style: TextStyle(fontSize: 28, fontWeight: FontWeight.w900, color: const Color(0xFF0F172A))),
                              ]
                          ),
                        ),
                        Container(
                          key: const ValueKey("dom-motif_launch_kpis_kpi_docs"),
                          padding: const EdgeInsets.all(16),
                          decoration: BoxDecoration(color: const Color(0xFFFFFFFF), border: Border.all(color: const Color(0xFFE2E8F0)), borderRadius: BorderRadius.circular(12)),
                          child: Column(
                            mainAxisSize: MainAxisSize.min,
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                              children: <Widget>[
                                Text('${widget.data["kpi_docs_label"] ?? "Docs pages"}', key: const ValueKey("dom-binding_kpi_docs_label"), style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: const Color(0xFF64748B))),
                                Text('${widget.data["kpi_docs_value"] ?? "8"}', key: const ValueKey("dom-binding_kpi_docs_value"), style: TextStyle(fontSize: 28, fontWeight: FontWeight.w900, color: const Color(0xFF0F172A))),
                              ]
                          ),
                        ),
                        Container(
                          key: const ValueKey("dom-motif_launch_kpis_kpi_assets"),
                          padding: const EdgeInsets.all(16),
                          decoration: BoxDecoration(color: const Color(0xFFFFFFFF), border: Border.all(color: const Color(0xFFE2E8F0)), borderRadius: BorderRadius.circular(12)),
                          child: Column(
                            mainAxisSize: MainAxisSize.min,
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                              children: <Widget>[
                                Text('${widget.data["kpi_assets_label"] ?? "Demo assets"}', key: const ValueKey("dom-binding_kpi_assets_label"), style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: const Color(0xFF64748B))),
                                Text('${widget.data["kpi_assets_value"] ?? "7"}', key: const ValueKey("dom-binding_kpi_assets_value"), style: TextStyle(fontSize: 28, fontWeight: FontWeight.w900, color: const Color(0xFF0F172A))),
                              ]
                          ),
                        ),
                        Container(
                          key: const ValueKey("dom-motif_launch_kpis_kpi_blockers"),
                          padding: const EdgeInsets.all(16),
                          decoration: BoxDecoration(color: const Color(0xFFFFFFFF), border: Border.all(color: const Color(0xFFE2E8F0)), borderRadius: BorderRadius.circular(12)),
                          child: Column(
                            mainAxisSize: MainAxisSize.min,
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                              children: <Widget>[
                                Text('${widget.data["kpi_blockers_label"] ?? "Blockers"}', key: const ValueKey("dom-binding_kpi_blockers_label"), style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: const Color(0xFF64748B))),
                                Text('${widget.data["kpi_blockers_value"] ?? "1"}', key: const ValueKey("dom-binding_kpi_blockers_value"), style: TextStyle(fontSize: 28, fontWeight: FontWeight.w900, color: const Color(0xFF0F172A))),
                              ]
                          ),
                        ),
                      ]
                    ),
                  ]
                ),
                Column(
                  key: const ValueKey("dom-region_status"),
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: <Widget>[
                    Column(
                      key: const ValueKey("dom-motif_launch_status_table"),
                      mainAxisSize: MainAxisSize.min,
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: <Widget>[
                        Wrap(
                          key: const ValueKey("dom-motif_launch_status_table_row_html"),
                          spacing: 12,
                          runSpacing: 12,
                          children: <Widget>[
                            Text('${widget.data["row_html_label"] ?? "HTML/Tailwind"}', key: const ValueKey("dom-binding_row_html_label"), style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: const Color(0xFF0F766E))),
                            Text('${widget.data["row_html_state"] ?? "Live link ready"}', key: const ValueKey("dom-binding_row_html_state"), style: TextStyle(fontSize: 28, fontWeight: FontWeight.w900, color: const Color(0xFF0F766E))),
                            Text('${widget.data["row_html_owner"] ?? "web"}', key: const ValueKey("dom-binding_row_html_owner"), style: TextStyle(fontSize: 28, fontWeight: FontWeight.w900, color: const Color(0xFF0F766E))),
                          ]
                        ),
                        Wrap(
                          key: const ValueKey("dom-motif_launch_status_table_row_react"),
                          spacing: 12,
                          runSpacing: 12,
                          children: <Widget>[
                            Text('${widget.data["row_react_label"] ?? "React TSX"}', key: const ValueKey("dom-binding_row_react_label"), style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: const Color(0xFF0F766E))),
                            Text('${widget.data["row_react_state"] ?? "Runtime page ready"}', key: const ValueKey("dom-binding_row_react_state"), style: TextStyle(fontSize: 28, fontWeight: FontWeight.w900, color: const Color(0xFF0F766E))),
                            Text('${widget.data["row_react_owner"] ?? "web"}', key: const ValueKey("dom-binding_row_react_owner"), style: TextStyle(fontSize: 28, fontWeight: FontWeight.w900, color: const Color(0xFF0F766E))),
                          ]
                        ),
                        Wrap(
                          key: const ValueKey("dom-motif_launch_status_table_row_swiftui"),
                          spacing: 12,
                          runSpacing: 12,
                          children: <Widget>[
                            Text('${widget.data["row_swiftui_label"] ?? "SwiftUI"}', key: const ValueKey("dom-binding_row_swiftui_label"), style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: const Color(0xFF64748B))),
                            Text('${widget.data["row_swiftui_state"] ?? "External simulator recording"}', key: const ValueKey("dom-binding_row_swiftui_state"), style: TextStyle(fontSize: 28, fontWeight: FontWeight.w900, color: const Color(0xFF0F172A))),
                            Text('${widget.data["row_swiftui_owner"] ?? "mobile"}', key: const ValueKey("dom-binding_row_swiftui_owner"), style: TextStyle(fontSize: 28, fontWeight: FontWeight.w900, color: const Color(0xFF0F172A))),
                          ]
                        ),
                        Wrap(
                          key: const ValueKey("dom-motif_launch_status_table_row_flutter"),
                          spacing: 12,
                          runSpacing: 12,
                          children: <Widget>[
                            Text('${widget.data["row_flutter_label"] ?? "Flutter"}', key: const ValueKey("dom-binding_row_flutter_label"), style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: const Color(0xFF64748B))),
                            Text('${widget.data["row_flutter_state"] ?? "External emulator recording"}', key: const ValueKey("dom-binding_row_flutter_state"), style: TextStyle(fontSize: 28, fontWeight: FontWeight.w900, color: const Color(0xFF0F172A))),
                            Text('${widget.data["row_flutter_owner"] ?? "mobile"}', key: const ValueKey("dom-binding_row_flutter_owner"), style: TextStyle(fontSize: 28, fontWeight: FontWeight.w900, color: const Color(0xFF0F172A))),
                          ]
                        ),
                      ]
                    ),
                  ]
                ),
                Column(
                  key: const ValueKey("dom-region_form"),
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: <Widget>[
                    if (_evaluateRule("show_mobile_note", _inputValues, widget.data))
                      Text('${widget.data["mobile_recording_note"] ?? "Mobile runtime recording is handed off because this Windows workspace has no simulator tooling."}', key: const ValueKey("dom-binding_mobile_recording_note"), style: TextStyle(fontSize: 16, fontWeight: FontWeight.w400, color: const Color(0xFFB45309))),
                    DropdownButtonFormField<String>(
                      key: const ValueKey("dom-input_phase_filter"),
                      decoration: InputDecoration(labelText: "Phase filter"),
                      value: _selectValue("phase_filter", const <String>["Demos", "Docs", "Mobile", "Launch"]),
                      items: <DropdownMenuItem<String>>[
                        DropdownMenuItem<String>(value: "Demos", child: Text("Demos")),
                        DropdownMenuItem<String>(value: "Docs", child: Text("Docs")),
                        DropdownMenuItem<String>(value: "Mobile", child: Text("Mobile")),
                        DropdownMenuItem<String>(value: "Launch", child: Text("Launch")),
                      ],
                      onChanged: (value) => setState(() => _inputValues["phase_filter"] = value ?? ''),
                    ),
                    TextField(
                      key: const ValueKey("dom-input_owner_email"),
                      controller: _owner_emailController,
                      decoration: InputDecoration(labelText: "Owner email"),
                      onChanged: (value) => setState(() => _inputValues["owner_email"] = value),
                    ),
                    SwitchListTile(
                      key: const ValueKey("dom-input_include_mobile"),
                      title: Text("Include mobile handoff"),
                      value: (_inputValues["include_mobile"] as bool?) ?? false,
                      onChanged: (value) => setState(() => _inputValues["include_mobile"] = value),
                    ),
                    ElevatedButton(
                      key: const ValueKey("dom-action_save_launch_review"),
                      onPressed: () => _dispatchAction("save_launch_review", "submit", "/launch-review", const <String>["phase_filter", "owner_email", "include_mobile", "launch_status", "kpi_blockers_value"]),
                      child: Text("Save launch review"),
                    ),
                  ]
                ),
              ]
          ),
        ),
      ),
    );
  }
}
