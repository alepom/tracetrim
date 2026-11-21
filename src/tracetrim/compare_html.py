"""`tracetrim compare ... --html FILE` (1.4): the comparison as one page, in the style of the telemetry report.

The page is built from the `tracetrim/compare@1` document that `--json` prints, and only from it, so its numbers
are that document's. Nothing is loaded from the network: the style and the language and theme switches are
those of the report (tracetrim.report), inline, without the live report's refresh. Five languages, as the
report; tables scroll inside their card, so the page holds at 390 px.
"""
from __future__ import annotations

import html

from . import i18n
from . import report as rp

# Every label in the five languages of the report, in the order of i18n.LANGS.
_LABELS: dict[str, tuple[str, str, str, str, str]] = {
    "title": ("Compare two configurations", "Confronto tra due configurazioni", "Comparación de dos configuraciones",
              "Comparaison de deux configurations", "两种配置的比较"),
    "subtitle": ("The same recorded runs, filtered under two recipes, each learned on the other runs.",
                 "Le stesse esecuzioni registrate, filtrate con due ricette, ciascuna appresa sulle altre esecuzioni.",
                 "Las mismas ejecuciones grabadas, filtradas con dos recetas, cada una aprendida en las demás ejecuciones.",
                 "Les mêmes exécutions enregistrées, filtrées selon deux recettes, chacune apprise sur les autres exécutions.",
                 "同一批已记录的运行,按两种方案过滤,每种方案都在其余运行上学习。"),
    "nav_method": ("Method", "Metodo", "Método", "Méthode", "方法"),
    "nav_tools": ("Tools", "Tool", "Herramientas", "Outils", "工具"),
    "nav_missed": ("Missed fields", "Campi mancati", "Campos perdidos", "Champs manqués", "遗漏字段"),
    "nav_whole": ("Sent whole", "Inviate intere", "Enviadas enteras", "Envoyés entiers", "整体发送"),
    "nav_div": ("Divergences", "Divergenze", "Divergencias", "Divergences", "差异"),
    "nav_expand": ("expand", "expand", "expand", "expand", "expand"),
    "method": ("Leave-one-out. Used fields judged with the rules of 1.1 for both sides. Prudent filtering with expand "
               "on all learned tools. No thresholds, promotions or sampling.",
               "Leave-one-out. I campi usati sono giudicati con le regole della 1.1 per entrambe le parti. Filtro prudente "
               "con expand su tutti i tool appresi. Nessuna soglia, promozione o campionamento.",
               "Leave-one-out. Los campos usados se juzgan con las reglas de la 1.1 en ambos lados. Filtrado prudente con "
               "expand en todas las herramientas aprendidas. Sin umbrales, promociones ni muestreo.",
               "Leave-one-out. Les champs utilisés sont jugés selon les règles de la 1.1 des deux côtés. Filtrage prudent "
               "avec expand sur tous les outils appris. Ni seuils, ni promotions, ni échantillonnage.",
               "留一法。两侧均按 1.1 的规则判断已使用的字段。对所有已学习的工具进行谨慎过滤并启用 expand。无阈值、无提升、无抽样。"),
    "tokens": ("Tokens: o200k_base, including the [trimmed] line. Deterministic annotation ids in replay; random "
               "production ids can vary by a few tokens per annotation.",
               "Token: o200k_base, compresa la riga [trimmed]. Identificativi di annotazione deterministici nel replay; "
               "quelli casuali in produzione possono variare di pochi token per annotazione.",
               "Tokens: o200k_base, incluida la línea [trimmed]. Identificadores de anotación deterministas en la "
               "repetición; los aleatorios en producción pueden variar unos pocos tokens por anotación.",
               "Tokens : o200k_base, ligne [trimmed] comprise. Identifiants d'annotation déterministes dans la relecture ; "
               "les identifiants aléatoires en production peuvent varier de quelques tokens par annotation.",
               "Token:o200k_base,包括 [trimmed] 行。回放中注释 ID 是确定的;生产中的随机 ID 每条注释可能相差几个 token。"),
    "read": ("Files read", "File letti", "Archivos leídos", "Fichiers lus", "读取的文件"),
    "calls_read": ("Calls read", "Chiamate lette", "Llamadas leídas", "Appels lus", "读取的调用"),
    "runs": ("Runs evaluated", "Esecuzioni valutate", "Ejecuciones evaluadas", "Exécutions évaluées", "评估的运行"),
    "outputs": ("Tool outputs evaluated", "Output dei tool valutati", "Salidas evaluadas", "Sorties évaluées",
                "评估的工具输出"),
    "warnings": ("Warnings", "Avvisi", "Avisos", "Avertissements", "警告"),
    "tool": ("Tool", "Tool", "Herramienta", "Outil", "工具"),
    "calls": ("Calls", "Chiamate", "Llamadas", "Appels", "调用"),
    "tokens_a": ("Tokens A", "Token A", "Tokens A", "Tokens A", "Token A"),
    "tokens_b": ("Tokens B", "Token B", "Tokens B", "Tokens B", "Token B"),
    "delta": ("Delta B−A", "Differenza B−A", "Diferencia B−A", "Écart B−A", "差值 B−A"),
    "miss": ("Calls with a miss A/B", "Chiamate con un campo mancato A/B", "Llamadas con un campo perdido A/B",
             "Appels avec un champ manqué A/B", "有遗漏的调用 A/B"),
    "cheaper": ("Sent whole, cheaper A/B", "Intere perché conviene A/B", "Enteras por ser más baratas A/B",
                "Entiers car moins chers A/B", "整体更便宜 A/B"),
    "total": ("Total", "Totale", "Total", "Total", "合计"),
    "missed_note": ("Fields visibly reused later in the run but hidden by a side: calls per side.",
                    "Campi riusati in modo visibile più avanti nell'esecuzione ma nascosti da una parte: chiamate per parte.",
                    "Campos reutilizados de forma visible más adelante en la ejecución pero ocultos por un lado: llamadas por lado.",
                    "Champs réutilisés de façon visible plus loin dans l'exécution mais masqués par un côté : appels par côté.",
                    "在运行后续中被明显复用、却被某一侧隐藏的字段:每侧的调用数。"),
    "field": ("Field", "Campo", "Campo", "Champ", "字段"),
    "whole_note": ("Calls served whole because the annotated, filtered output would cost at least as much.",
                   "Chiamate inviate intere perché l'output filtrato con l'annotazione costerebbe almeno altrettanto.",
                   "Llamadas enviadas enteras porque la salida filtrada con la anotación costaría lo mismo o más.",
                   "Appels envoyés entiers parce que la sortie filtrée annotée coûterait au moins autant.",
                   "因为带注释的过滤输出成本不低于原输出而整体发送的调用。"),
    "side": ("Side", "Parte", "Lado", "Côté", "侧"),
    "run": ("Run", "Esecuzione", "Ejecución", "Exécution", "运行"),
    "call": ("Call", "Chiamata", "Llamada", "Appel", "调用"),
    "div_note": ("Examples of a field hidden by one side only (at most one per tool, three in all).",
                 "Esempi di un campo nascosto da una sola parte (al massimo uno per tool, tre in tutto).",
                 "Ejemplos de un campo oculto por un solo lado (como máximo uno por herramienta, tres en total).",
                 "Exemples d'un champ masqué par un seul côté (au plus un par outil, trois en tout).",
                 "仅被一侧隐藏的字段示例(每个工具最多一个,共三个)。"),
    "hidden_by": ("Hidden only by", "Nascosto solo da", "Oculto solo por", "Masqué seulement par", "仅被隐藏于"),
    "none": ("None.", "Nessuno.", "Ninguno.", "Aucun.", "无。"),
    "expand_seen": ("expand requests in the supplied traces", "Richieste expand nelle tracce fornite",
                    "Solicitudes expand en las trazas aportadas", "Requêtes expand dans les traces fournies",
                    "所提供轨迹中的 expand 请求"),
    "limit": ("Only expand requests recorded in the supplied trace files are counted. Replay cannot predict expand "
              "requests or changes to the final answer.",
              "Si contano solo le richieste expand registrate nei file di traccia forniti. Il replay non può prevedere "
              "richieste expand né cambiamenti della risposta finale.",
              "Solo se cuentan las solicitudes expand registradas en los archivos de traza aportados. La repetición no "
              "puede prever solicitudes expand ni cambios en la respuesta final.",
              "Seules les requêtes expand enregistrées dans les fichiers de traces fournis sont comptées. La relecture ne "
              "peut prévoir ni les requêtes expand ni les changements de la réponse finale.",
              "只统计所提供轨迹文件中记录的 expand 请求。回放无法预测 expand 请求或最终答案的变化。"),
}


# The reasons why compare excludes a run (compare.read_runs) and the small-sample warning, in the five languages.
_REASONS: dict[str, tuple[str, str, str, str, str]] = {
    "missing final answer": ("missing final answer", "manca la risposta finale", "falta la respuesta final",
                             "réponse finale absente", "缺少最终答案"),
    "missing original tool output": ("missing original tool output", "manca l'output originale di un tool",
                                     "falta la salida original de una herramienta",
                                     "sortie d'outil originale absente", "缺少工具的原始输出"),
    "tool output contains a trimming annotation": ("a tool output contains a trimming annotation",
                                                   "un output contiene un'annotazione di filtro",
                                                   "una salida contiene una anotación de filtrado",
                                                   "une sortie contient une annotation de filtrage",
                                                   "某个工具输出包含过滤注释"),
}
_WARN = {"excluded": ("excluded", "esclusa", "excluida", "exclue", "已排除"),
         "small_sample": ("small sample: {runs} eligible runs (fewer than {minimum})",
                          "campione piccolo: {runs} esecuzioni idonee (meno di {minimum})",
                          "muestra pequeña: {runs} ejecuciones válidas (menos de {minimum})",
                          "petit échantillon : {runs} exécutions retenues (moins de {minimum})",
                          "样本较小:{runs} 个有效运行(少于 {minimum} 个)")}


def _warning(w: dict) -> str:
    """One warning in every language; an unknown reason is shown as compare wrote it."""
    def render(lang):
        i = i18n.LANGS.index(lang)
        if w["code"] == "small_sample":
            return html.escape(_WARN["small_sample"][i].format(runs=w["runs"], minimum=w["minimum"]))
        reason = _REASONS.get(w["reason"], (w["reason"],) * 5)[i]
        return f"<code>{html.escape(w['file'])}</code>: {html.escape(_WARN['excluded'][i])} ({html.escape(reason)})"
    return rp._langs(render)


def label(key: str, lang: str) -> str:
    return _LABELS[key][i18n.LANGS.index(lang)]


def _t(key: str) -> str:
    return rp._langs(lambda lang: html.escape(label(key, lang)))


def _num(x) -> str:
    return rp._langs(lambda lang: i18n.num(x, lang))


def _signed(x) -> str:
    return rp._langs(lambda lang: ("+" if x > 0 else "−" if x < 0 else "") + i18n.num(abs(x), lang))


RIGHT = ' class="r"'


def _table(head: list[str], rows: list[list[str]], numeric_from: int = 1) -> str:
    th = "".join(f'<th{RIGHT if i >= numeric_from else ""}>{h}</th>' for i, h in enumerate(head))
    body = "".join("<tr>" + "".join(f'<td{RIGHT if i >= numeric_from else ""}>{c}</td>' for i, c in enumerate(r))
                   + "</tr>" for r in rows)
    return f'<div class="scroll"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def _code(s) -> str:
    return f"<code>{html.escape(str(s))}</code>"


def _section(sid: str, key: str, body: str) -> str:
    return f'<section class="sec" id="{sid}"><h2>{_t(key)}</h2>{body}</section>'


def render(data: dict) -> str:
    if data.get("format") != "tracetrim/compare@1":
        raise ValueError("not a tracetrim/compare@1 document")
    totals = data["totals"]
    configs = "".join(f'<p class="method"><b>{side.upper()}</b> '
                      + _code(", ".join(f"{k}={str(v).lower()}" for k, v in c.items())) + "</p>"
                      for side, c in data["configurations"].items())
    tiles = [("read", len(data["inputs"])), ("calls_read", sum(i["calls_read"] for i in data["inputs"])),
             ("runs", data["eligible_runs"]), ("outputs", totals["calls"])]
    tiles_html = "".join(f'<div class="tile"><div class="v">{_num(v)}</div><div class="k">{_t(k)}</div></div>'
                         for k, v in tiles)
    # Documents written before 1.4 have only the English text of the warnings.
    items = ([_warning(w) for w in data["warning_details"]] if "warning_details" in data
             else [html.escape(w) for w in data["warnings"]])
    warnings = ("<h3>" + _t("warnings") + "</h3><ul>" + "".join(f"<li>{w}</li>" for w in items) + "</ul>") if items else ""
    method = (f'<p class="sub">{_t("subtitle")}</p><p class="method">{_t("method")}</p>'
              f'<p class="method">{_t("tokens")}</p>{configs}<div class="tiles">{tiles_html}</div>{warnings}')

    def tool_row(name, row, bold=False):
        cells = [html.escape(name), _num(row["calls"]), _num(row["a"]["tokens"]), _num(row["b"]["tokens"]),
                 _signed(row["delta"]["tokens"]), f'{row["a"]["miss_calls"]}/{row["b"]["miss_calls"]}',
                 f'{row["a"]["full_cheaper"]}/{row["b"]["full_cheaper"]}']
        return [f"<b>{c}</b>" for c in cells] if bold else cells
    tools = _table([_t(k) for k in ("tool", "calls", "tokens_a", "tokens_b", "delta", "miss", "cheaper")],
                   [tool_row(n, r) for n, r in data["tools"].items()]
                   + [tool_row(label("total", "en"), totals, bold=True)])
    # The total row's name in every language.
    tools = tools.replace(f"<b>{html.escape(label('total', 'en'))}</b>", f"<b>{_t('total')}</b>", 1)

    missed_rows = [[html.escape(t), _code(f), _num(n["a"]), _num(n["b"])]
                   for t, row in data["tools"].items() for f, n in row["missed_fields"].items()]
    missed = f'<p class="method">{_t("missed_note")}</p>' + (
        _table([_t("tool"), _t("field"), "A", "B"], missed_rows, numeric_from=2) if missed_rows
        else f'<p>{_t("none")}</p>')
    whole_rows = [[",".join(s.upper() for s in w["sides"]), html.escape(str(w["run"])), _num(w["call"]),
                   html.escape(w["tool"])] for w in data["full_cheaper_calls"]]
    whole = f'<p class="method">{_t("whole_note")}</p>' + (
        _table([_t("side"), _t("run"), _t("call"), _t("tool")], whole_rows, numeric_from=9) if whole_rows
        else f'<p>{_t("none")}</p>')
    div_rows = [[html.escape(str(d["run"])), _num(d["call"]), html.escape(d["tool"]), _code(d["field"]),
                 d["hidden_by"].upper()] for d in data["divergences"]]
    divergences = f'<p class="method">{_t("div_note")}</p>' + (
        _table([_t("run"), _t("call"), _t("tool"), _t("field"), _t("hidden_by")], div_rows, numeric_from=9)
        if div_rows else f'<p>{_t("none")}</p>')
    expand = (f'<div class="tiles"><div class="tile"><div class="v">{_num(data["observed_expand"])}</div>'
              f'<div class="k">{_t("expand_seen")}</div></div></div><p class="method">{_t("limit")}</p>')

    sections = [("method", "nav_method", method), ("tools", "nav_tools", tools), ("missed", "nav_missed", missed),
                ("whole", "nav_whole", whole), ("divergences", "nav_div", divergences),
                ("expand", "nav_expand", expand)]
    nav = "".join(f'<a href="#{sid}">{_t(key)}</a>' for sid, key, _ in sections)
    body = (f'<section class="sec" id="top"><div class="intro"><h1>{_t("title")}</h1></div></section>'
            + "".join(_section(sid, key, b) for sid, key, b in sections))
    langs = "".join(f'<option value="{lg}">{html.escape(i18n.NAMES[lg])}</option>' for lg in i18n.LANGS)
    theme = "".join(f'<span class="tl" data-theme-label="{t}"{"" if t == "auto" else " hidden"}>{rp._t("theme_" + t)}</span>'
                    for t in ("auto", "light", "dark"))
    # The report's script up to its theme switch: language and theme, without the live refresh or anything else.
    script = rp._JS[:rp._JS.index("// The section in view")]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>tracetrim · Compare</title>
<script>{rp._HEAD_JS}</script>
<style>{rp._CSS}</style></head>
<body class="en">
<header class="top"><div class="top-in">
<div class="brand"><span class="logo">{rp._LOGO}</span><b>tracetrim</b><span class="crumb">{_t("title")}</span></div>
<div class="ctl"><label class="lsel" title="Language">{rp._GLOBE}<select id="lang" aria-label="Language" onchange="setLang(this.value)">{langs}</select></label>
<button class="tbtn" type="button" onclick="cycleTheme()" title="{html.escape(rp.L['theme'][0])}" aria-label="{html.escape(rp.L['theme'][0])}">{rp._THEME_ICON}{theme}</button></div>
</div><nav class="secnav" aria-label="Sections"><div class="nav-in">{nav}</div></nav></header>
<main>
{body}
</main>
<script>{script}</script></body></html>
"""
