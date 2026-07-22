from __future__ import annotations

import hashlib

from viewspec.app_pretext_runtime import (
    PRETEXT_RUNTIME_PATH,
    generate_pretext_runtime_typescript,
)


def test_pretext_runtime_path_and_source_are_deterministic() -> None:
    first = generate_pretext_runtime_typescript()
    second = generate_pretext_runtime_typescript()

    assert PRETEXT_RUNTIME_PATH == "src/viewspec_pretext.ts"
    assert first == second
    assert first.endswith("\n")
    assert hashlib.sha256(first.encode("utf-8")).hexdigest() == hashlib.sha256(second.encode("utf-8")).hexdigest()


def test_pretext_runtime_pins_protocol_engine_and_browser_entrypoint() -> None:
    source = generate_pretext_runtime_typescript()

    assert 'import { prepare, layout, type PreparedText } from "@chenglou/pretext";' in source
    assert 'import { setLocale } from "@chenglou/pretext";' in source
    assert 'const PROFILE = "viewspec_pretext_native_dom_v1" as const;' in source
    assert 'const PROTOCOL = "viewspec.pretext-runtime-v1" as const;' in source
    assert 'name: "pretext" as const' in source
    assert 'package: "@chenglou/pretext" as const' in source
    assert 'version: "0.0.8" as const' in source
    assert "export function installViewSpecPretextProbe(): void" in source
    assert "window.__viewspecPretextProbe = probe;" in source
    assert "__viewspecPretextProbe?: typeof probe;" in source
    assert "export async function probe(" in source


def test_pretext_runtime_reads_only_exact_targets_and_preserves_native_dom() -> None:
    source = generate_pretext_runtime_typescript()

    assert "document.getElementById(surface.surface_id)" in source
    assert 'target.getAttribute("data-ir-id") !== surface.ir_id' in source
    assert "surface_id: candidate.surface_id" in source
    assert "ir_id: candidate.ir_id" in source
    assert "document.querySelector" not in source
    assert ".querySelector(" not in source
    assert ".querySelectorAll(" not in source
    assert ".innerHTML" not in source
    assert ".outerHTML" not in source
    assert ".appendChild(" not in source
    assert ".replaceChildren(" not in source
    assert "range.selectNodeContents(element);" in source
    assert "Array.from(range.getClientRects())" in source


def test_pretext_runtime_waits_for_fonts_hashes_text_and_never_reports_raw_text() -> None:
    source = generate_pretext_runtime_typescript()

    assert "document.fonts.ready" in source
    assert 'globalThis.crypto.subtle.digest("SHA-256", digestInput)' in source
    assert "input_sha256: string;" in source
    assert "input_bytes: number;" in source
    assert "const earlyDigest = await preliminaryDigest(transformedText, locale);" in source
    assert 'statusItem(input, surface, "hidden", "target_hidden", earlyDigest, textBytes.byteLength)' in source
    assert "transformed_text:" not in source
    assert "raw_text:" not in source
    assert "MAX_TEXT_BYTES = 16 * 1024" in source
    assert "MAX_TOTAL_TEXT_BYTES = 256 * 1024" in source
    assert "MAX_SURFACES = 512" in source


def test_pretext_runtime_caches_prepare_without_width_and_lays_out_each_probe() -> None:
    source = generate_pretext_runtime_typescript()

    cache_key = source[
        source.index("const cacheKey = JSON.stringify([") : source.index(
            "]);", source.index("const cacheKey = JSON.stringify([")
        )
    ]
    assert "measurementText" in cache_key
    assert "config.font" in cache_key
    assert "config.whiteSpace" in cache_key
    assert "config.wordBreak" in cache_key
    assert "config.letterSpacing" in cache_key
    assert "locale" in cache_key
    assert "config.overflowWrap" not in cache_key
    assert "width" not in cache_key
    assert "const cached = preparedCache.get(key);" in source
    assert "setLocale(locale);" in source
    assert "const prepared = prepare(text, config.font" in source
    assert (
        "const predicted = layout(prepared, width + CHROMIUM_ARIAL_LAYOUT_FIT_TOLERANCE_PX, config.lineHeight);"
        in source
    )
    assert "const inputSha256 = await sha256String(cacheKey);" in source
    assert "prepare_calls: prepareCalls" in source
    assert "unique_inputs: preparedCache.size" in source
    assert "layout_calls: layoutCalls" in source
    assert "cache_hits: cacheHits" in source


def test_pretext_runtime_enforces_bounded_supported_style_profile() -> None:
    source = generate_pretext_runtime_typescript()

    assert 'style.writingMode !== "horizontal-tb"' in source
    assert 'style.whiteSpace !== "normal" && style.whiteSpace !== "pre-wrap"' in source
    assert 'style.wordBreak !== "normal" && style.wordBreak !== "keep-all"' in source
    assert 'style.overflowWrap !== "anywhere"' in source
    assert "addAnywhereBreakOpportunities(transformedText, locale)" in source
    assert 'style.letterSpacing !== "normal"' in source
    assert 'if (style.lineHeight === "normal")' in source
    assert "lineHeight = fontSize * 1.2;" in source
    assert 'lineHeightSource = "normal-1.2x-font-size";' in source
    assert '"-apple-system"' in source
    assert '"system-ui"' in source
    assert "UNSAFE_SYSTEM_FAMILIES.has(firstFamily.toLowerCase())" in source
    assert 'const SUPPORTED_FONT_FAMILY = "arial";' in source
    assert "firstFamily.toLowerCase() !== SUPPORTED_FONT_FAMILY" in source
    assert 'return { ok: false, reason: "unsafe_system_font" };' in source
    assert 'document.fonts.check(font, transformedText || " ")' in source
    assert 'case "uppercase":' in source
    assert 'case "lowercase":' in source
    assert 'case "capitalize":' in source


def test_pretext_runtime_verdict_is_line_count_and_overflow_based() -> None:
    source = generate_pretext_runtime_typescript()

    assert "predicted.lineCount !== lineTops.length" in source
    assert "target.scrollWidth > target.clientWidth + OVERFLOW_EPSILON_PX" in source
    assert "target.scrollHeight > target.clientHeight + OVERFLOW_EPSILON_PX" in source
    assert "const CHROMIUM_ARIAL_LAYOUT_FIT_TOLERANCE_PX = 1;" in source
    assert "available_width: Number(width.toFixed(6))" in source
    assert 'reason = "line_count_mismatch";' in source
    assert 'reason = "horizontal_overflow";' in source
    assert 'reason = "vertical_overflow";' in source
    assert "predicted.height ===" not in source
    assert "predicted.height !==" not in source
    assert "horizontalClipped = horizontalOverflow" in source
    assert "verticalClipped = verticalOverflow" in source


def test_pretext_runtime_has_explicit_item_statuses_for_fail_closed_adapter() -> None:
    source = generate_pretext_runtime_typescript()

    assert 'type ProbeStatus = "passed" | "hidden" | "unsupported" | "failed";' in source
    assert 'statusItem(input, surface, "failed", "target_missing")' in source
    assert 'statusItem(input, surface, "failed", "target_ir_mismatch")' in source
    assert 'statusItem(input, surface, "unsupported", styleResult.reason' in source
    assert 'item.status === "failed" || item.status === "unsupported"' in source
    assert "errors: errorsFor(items)" in source
    assert 'browser: "chromium"' in source
    assert 'font_status: "loaded" | "failed" | "unsupported"' in source
    assert 'document.documentElement.lang || navigator.language || "en-US"' in source


def test_pretext_runtime_accounts_for_visible_empty_text_without_false_mismatch() -> None:
    source = generate_pretext_runtime_typescript()

    assert "if (isHidden(target, style) || transformedText.length === 0)" in source
    assert 'const EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb924' in source
