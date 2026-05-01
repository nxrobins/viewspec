import SwiftUI

public struct ViewSpecActionIntent {
    public let id: String
    public let kind: String
    public let targetRef: String
    public let payloadBindings: [String]
    public let payload: [String: Any]
}

public struct ViewSpecView: View {
    public var data: [String: Any]
    public var onAction: ((ViewSpecActionIntent) -> Void)?
    @State private var inputValues: [String: Any] = ["include_mobile": true, "owner_email": "launch@viewspec.dev", "phase_filter": "Demos"]
    private let compiledPayloadValues: [String: Any] = ["kpi_blockers_value": "1", "launch_status": "On track"]
    private let visibilityRules: [String: [String: Any]] = ["show_mobile_note": ["compareValue": "Mobile", "compareValueType": "string", "fallbackValue": "Demos", "fallbackValueType": "string", "id": "show_mobile_note", "operator": "EQUALS", "sourceKind": "input", "sourceRef": "phase_filter", "targetRef": "binding:mobile_recording_note"]]

    public init(data: [String: Any] = [:], onAction: ((ViewSpecActionIntent) -> Void)? = nil) {
        self.data = data
        self.onAction = onAction
    }

    private func unwrapOptionalValue(_ value: Any) -> Any? {
        let mirror = Mirror(reflecting: value)
        guard mirror.displayStyle == .optional else { return value }
        return mirror.children.first?.value
    }

    private func textValue(_ key: String, fallback: String) -> String {
        guard let rawValue = data[key], let value = unwrapOptionalValue(rawValue) else { return fallback }
        return String(describing: value)
    }

    private func collectPayload(_ payloadBindings: [String]) -> [String: Any] {
        var payload: [String: Any] = [:]
        for bindingId in payloadBindings {
            if let value = inputValues[bindingId] {
                payload[bindingId] = value
            } else if let value = compiledPayloadValues[bindingId] {
                payload[bindingId] = value
            }
        }
        return payload
    }

    private func isEmptyRuleValue(_ value: Any?) -> Bool {
        guard let value = value else { return true }
        if let text = value as? String { return text.isEmpty }
        if let list = value as? [Any] { return list.isEmpty }
        return false
    }

    private func numericRuleValue(_ value: Any?) -> Double? {
        if let value = value as? Int { return Double(value) }
        if let value = value as? Double { return value }
        if let value = value as? Float { return Double(value) }
        return nil
    }

    private func ruleValueType(_ value: Any?) -> String {
        guard let value = value else { return "null" }
        if value is Bool { return "boolean" }
        if numericRuleValue(value) != nil { return "number" }
        if value is String { return "string" }
        if value is [Any] { return "array" }
        return "object"
    }

    private func sameScalar(_ left: Any?, _ right: Any?) -> Bool {
        let leftType = ruleValueType(left)
        let rightType = ruleValueType(right)
        guard ["null", "string", "number", "boolean"].contains(leftType), leftType == rightType else { return false }
        if leftType == "null" { return true }
        if leftType == "number" { return numericRuleValue(left) == numericRuleValue(right) }
        if leftType == "boolean" { return (left as? Bool) == (right as? Bool) }
        return (left as? String) == (right as? String)
    }

    private func ruleValue(_ rule: [String: Any], key: String) -> Any? {
        if (rule["\(key)Type"] as? String) == "null" { return nil }
        return rule[key]
    }

    private func ruleSourceValue(_ rule: [String: Any], inputValues: [String: Any], data: [String: Any]) -> Any? {
        let sourceRef = rule["sourceRef"] as? String ?? ""
        if (rule["sourceKind"] as? String) == "input" {
            if let value = inputValues[sourceRef] { return unwrapOptionalValue(value) ?? ruleValue(rule, key: "fallbackValue") }
            return ruleValue(rule, key: "fallbackValue")
        }
        if let value = data[sourceRef] { return unwrapOptionalValue(value) ?? ruleValue(rule, key: "fallbackValue") }
        return ruleValue(rule, key: "fallbackValue")
    }

    private func evaluateRule(_ ruleId: String, inputValues: [String: Any], data: [String: Any]) -> Bool {
        guard let rule = visibilityRules[ruleId] else { return true }
        let value = ruleSourceValue(rule, inputValues: inputValues, data: data)
        let op = rule["operator"] as? String ?? ""
        if op == "IS_EMPTY" { return isEmptyRuleValue(value) }
        if op == "NOT_EMPTY" { return !isEmptyRuleValue(value) }
        if op == "IS_TRUE" { return (value as? Bool) == true }
        if op == "IS_FALSE" { return (value as? Bool) == false }
        if op == "EQUALS" { return sameScalar(value, ruleValue(rule, key: "compareValue")) }
        if op == "NOT_EQUALS" { return !sameScalar(value, ruleValue(rule, key: "compareValue")) }
        return true
    }

    private func dispatchAction(id: String, kind: String, targetRef: String, payloadBindings: [String]) {
        onAction?(ViewSpecActionIntent(
            id: id,
            kind: kind,
            targetRef: targetRef,
            payloadBindings: payloadBindings,
            payload: collectPayload(payloadBindings)
        ))
    }

    public var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 8.32) {
                VStack(alignment: .leading, spacing: 12) {
                    Text(textValue("launch_title", fallback: "Launch Operations Dashboard"))
                    .font(.system(size: 16, weight: .bold))
                    .foregroundColor(Color(red: 0.122, green: 0.161, blue: 0.216))
                    .opacity(0.92)
                    .accessibilityIdentifier("dom-binding_launch_title")
                    Text(textValue("launch_status", fallback: "On track"))
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(Color(red: 0.059, green: 0.463, blue: 0.431))
                    .opacity(0.92)
                    .accessibilityIdentifier("dom-binding_launch_status")
                    .padding(.horizontal, 12)
                    .padding(.vertical, 6)
                    .background(Color(red: 0.941, green: 0.992, blue: 0.980))
                    .cornerRadius(999)
                    Text(textValue("launch_summary", fallback: "One ViewSpec source proves web, React, iOS, and Android compilation."))
                    .font(.system(size: 16, weight: .regular))
                    .foregroundColor(Color(red: 0.122, green: 0.161, blue: 0.216))
                    .opacity(0.92)
                    .accessibilityIdentifier("dom-binding_launch_summary")
                }
                .accessibilityIdentifier("dom-region_header")
                LazyVGrid(columns: Array(repeating: GridItem(.flexible()), count: 2), spacing: 16) {
                    VStack(alignment: .leading, spacing: 12) {
                        VStack(alignment: .leading, spacing: 12) {
                            Text(textValue("kpi_emitters_label", fallback: "Emitter targets"))
                            .font(.system(size: 12, weight: .heavy))
                            .foregroundColor(Color(red: 0.122, green: 0.161, blue: 0.216))
                            .accessibilityIdentifier("dom-binding_kpi_emitters_label")
                            Text(textValue("kpi_emitters_value", fallback: "4"))
                            .font(.system(size: 28, weight: .black))
                            .foregroundColor(Color(red: 0.122, green: 0.161, blue: 0.216))
                            .opacity(0.92)
                            .accessibilityIdentifier("dom-binding_kpi_emitters_value")
                        }
                        .padding(16)
                        .background(Color.white)
                        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color(red: 0.886, green: 0.910, blue: 0.941)))
                        .cornerRadius(12)
                        .accessibilityIdentifier("dom-motif_launch_kpis_kpi_emitters")
                        VStack(alignment: .leading, spacing: 12) {
                            Text(textValue("kpi_docs_label", fallback: "Docs pages"))
                            .font(.system(size: 12, weight: .bold))
                            .foregroundColor(Color(red: 0.122, green: 0.161, blue: 0.216))
                            .opacity(0.92)
                            .accessibilityIdentifier("dom-binding_kpi_docs_label")
                            Text(textValue("kpi_docs_value", fallback: "8"))
                            .font(.system(size: 28, weight: .black))
                            .foregroundColor(Color(red: 0.122, green: 0.161, blue: 0.216))
                            .opacity(0.92)
                            .accessibilityIdentifier("dom-binding_kpi_docs_value")
                        }
                        .padding(16)
                        .background(Color.white)
                        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color(red: 0.886, green: 0.910, blue: 0.941)))
                        .cornerRadius(12)
                        .accessibilityIdentifier("dom-motif_launch_kpis_kpi_docs")
                        VStack(alignment: .leading, spacing: 12) {
                            Text(textValue("kpi_assets_label", fallback: "Demo assets"))
                            .font(.system(size: 12, weight: .bold))
                            .foregroundColor(Color(red: 0.122, green: 0.161, blue: 0.216))
                            .opacity(0.92)
                            .accessibilityIdentifier("dom-binding_kpi_assets_label")
                            Text(textValue("kpi_assets_value", fallback: "7"))
                            .font(.system(size: 28, weight: .black))
                            .foregroundColor(Color(red: 0.122, green: 0.161, blue: 0.216))
                            .opacity(0.92)
                            .accessibilityIdentifier("dom-binding_kpi_assets_value")
                        }
                        .padding(16)
                        .background(Color.white)
                        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color(red: 0.886, green: 0.910, blue: 0.941)))
                        .cornerRadius(12)
                        .accessibilityIdentifier("dom-motif_launch_kpis_kpi_assets")
                        VStack(alignment: .leading, spacing: 12) {
                            Text(textValue("kpi_blockers_label", fallback: "Blockers"))
                            .font(.system(size: 12, weight: .bold))
                            .foregroundColor(Color(red: 0.122, green: 0.161, blue: 0.216))
                            .opacity(0.92)
                            .accessibilityIdentifier("dom-binding_kpi_blockers_label")
                            Text(textValue("kpi_blockers_value", fallback: "1"))
                            .font(.system(size: 28, weight: .black))
                            .foregroundColor(Color(red: 0.122, green: 0.161, blue: 0.216))
                            .opacity(0.92)
                            .accessibilityIdentifier("dom-binding_kpi_blockers_value")
                        }
                        .padding(16)
                        .background(Color.white)
                        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color(red: 0.886, green: 0.910, blue: 0.941)))
                        .cornerRadius(12)
                        .accessibilityIdentifier("dom-motif_launch_kpis_kpi_blockers")
                    }
                    .accessibilityIdentifier("dom-motif_launch_kpis")
                }
                .accessibilityIdentifier("dom-region_kpis")
                VStack(alignment: .leading, spacing: 12) {
                    VStack(alignment: .leading, spacing: 12) {
                        VStack(alignment: .leading, spacing: 12) {
                            Text(textValue("row_html_label", fallback: "HTML/Tailwind"))
                            .font(.system(size: 12, weight: .bold))
                            .foregroundColor(Color(red: 0.059, green: 0.463, blue: 0.431))
                            .opacity(0.92)
                            .accessibilityIdentifier("dom-binding_row_html_label")
                            Text(textValue("row_html_state", fallback: "Live link ready"))
                            .font(.system(size: 28, weight: .black))
                            .foregroundColor(Color(red: 0.059, green: 0.463, blue: 0.431))
                            .opacity(0.92)
                            .accessibilityIdentifier("dom-binding_row_html_state")
                            Text(textValue("row_html_owner", fallback: "web"))
                            .font(.system(size: 28, weight: .black))
                            .foregroundColor(Color(red: 0.059, green: 0.463, blue: 0.431))
                            .opacity(0.92)
                            .accessibilityIdentifier("dom-binding_row_html_owner")
                        }
                        .accessibilityIdentifier("dom-motif_launch_status_table_row_html")
                        VStack(alignment: .leading, spacing: 12) {
                            Text(textValue("row_react_label", fallback: "React TSX"))
                            .font(.system(size: 12, weight: .bold))
                            .foregroundColor(Color(red: 0.059, green: 0.463, blue: 0.431))
                            .opacity(0.92)
                            .accessibilityIdentifier("dom-binding_row_react_label")
                            Text(textValue("row_react_state", fallback: "Runtime page ready"))
                            .font(.system(size: 28, weight: .black))
                            .foregroundColor(Color(red: 0.059, green: 0.463, blue: 0.431))
                            .opacity(0.92)
                            .accessibilityIdentifier("dom-binding_row_react_state")
                            Text(textValue("row_react_owner", fallback: "web"))
                            .font(.system(size: 28, weight: .black))
                            .foregroundColor(Color(red: 0.059, green: 0.463, blue: 0.431))
                            .opacity(0.92)
                            .accessibilityIdentifier("dom-binding_row_react_owner")
                        }
                        .accessibilityIdentifier("dom-motif_launch_status_table_row_react")
                        VStack(alignment: .leading, spacing: 12) {
                            Text(textValue("row_swiftui_label", fallback: "SwiftUI"))
                            .font(.system(size: 12, weight: .bold))
                            .foregroundColor(Color(red: 0.122, green: 0.161, blue: 0.216))
                            .opacity(0.92)
                            .accessibilityIdentifier("dom-binding_row_swiftui_label")
                            Text(textValue("row_swiftui_state", fallback: "External simulator recording"))
                            .font(.system(size: 28, weight: .black))
                            .foregroundColor(Color(red: 0.122, green: 0.161, blue: 0.216))
                            .opacity(0.92)
                            .accessibilityIdentifier("dom-binding_row_swiftui_state")
                            Text(textValue("row_swiftui_owner", fallback: "mobile"))
                            .font(.system(size: 28, weight: .black))
                            .foregroundColor(Color(red: 0.122, green: 0.161, blue: 0.216))
                            .opacity(0.92)
                            .accessibilityIdentifier("dom-binding_row_swiftui_owner")
                        }
                        .accessibilityIdentifier("dom-motif_launch_status_table_row_swiftui")
                        VStack(alignment: .leading, spacing: 12) {
                            Text(textValue("row_flutter_label", fallback: "Flutter"))
                            .font(.system(size: 12, weight: .bold))
                            .foregroundColor(Color(red: 0.122, green: 0.161, blue: 0.216))
                            .opacity(0.92)
                            .accessibilityIdentifier("dom-binding_row_flutter_label")
                            Text(textValue("row_flutter_state", fallback: "External emulator recording"))
                            .font(.system(size: 28, weight: .black))
                            .foregroundColor(Color(red: 0.122, green: 0.161, blue: 0.216))
                            .opacity(0.92)
                            .accessibilityIdentifier("dom-binding_row_flutter_state")
                            Text(textValue("row_flutter_owner", fallback: "mobile"))
                            .font(.system(size: 28, weight: .black))
                            .foregroundColor(Color(red: 0.122, green: 0.161, blue: 0.216))
                            .opacity(0.92)
                            .accessibilityIdentifier("dom-binding_row_flutter_owner")
                        }
                        .accessibilityIdentifier("dom-motif_launch_status_table_row_flutter")
                    }
                    .accessibilityIdentifier("dom-motif_launch_status_table")
                }
                .accessibilityIdentifier("dom-region_status")
                VStack(alignment: .leading, spacing: 16) {
                    if evaluateRule("show_mobile_note", inputValues: inputValues, data: data) {
                    Text(textValue("mobile_recording_note", fallback: "Mobile runtime recording is handed off because this Windows workspace has no simulator tooling."))
                    .font(.system(size: 16, weight: .regular))
                    .foregroundColor(Color(red: 0.706, green: 0.325, blue: 0.035))
                    .opacity(0.92)
                    .accessibilityIdentifier("dom-binding_mobile_recording_note")
                    }
                    Picker("Phase filter", selection: Binding<String>(
                        get: { inputValues["phase_filter"] as? String ?? "Demos" },
                        set: { inputValues["phase_filter"] = $0 }
                    )) {
                        Text("Demos").tag("Demos")
                        Text("Docs").tag("Docs")
                        Text("Mobile").tag("Mobile")
                        Text("Launch").tag("Launch")
                    }
                    .pickerStyle(.menu)
                    .accessibilityIdentifier("dom-input_phase_filter")
                    TextField("Owner email", text: Binding<String>(
                        get: { inputValues["owner_email"] as? String ?? "" },
                        set: { inputValues["owner_email"] = $0 }
                    ))
                    .textFieldStyle(.roundedBorder)
                    .accessibilityIdentifier("dom-input_owner_email")
                    Toggle("Include mobile handoff", isOn: Binding<Bool>(
                        get: { inputValues["include_mobile"] as? Bool ?? false },
                        set: { inputValues["include_mobile"] = $0 }
                    ))
                    .accessibilityIdentifier("dom-input_include_mobile")
                    Button(action: { dispatchAction(id: "save_launch_review", kind: "submit", targetRef: "/launch-review", payloadBindings: ["phase_filter", "owner_email", "include_mobile", "launch_status", "kpi_blockers_value"]) }) {
                        Text("Save launch review")
                    }
                    .buttonStyle(.borderedProminent)
                    .accessibilityIdentifier("dom-action_save_launch_review")
                }
                .accessibilityIdentifier("dom-region_form")
            }
            .padding(24)
        }
        .accessibilityIdentifier("dom-region_root")
    }
}
