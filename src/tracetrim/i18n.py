"""The report's languages beyond English and Italian: Spanish, French, Simplified Chinese (since 1.1).

English and Italian live next to the code that uses them (tracetrim.report: `L`, `ACTION_TEXT`, the rules of
`suggestions`); this module holds the other three, keyed the same way, and the number formats of every language.
A key missing here falls back to English: tests/test_i18n.py checks that none is missing.
"""
from __future__ import annotations

LANGS = ("en", "it", "es", "fr", "zh")
NAMES = {"en": "English", "it": "Italiano", "es": "Español", "fr": "Français", "zh": "中文"}
EXTRA = ("es", "fr", "zh")

NNBSP, NBSP = " ", " "


def num(x, lang: str) -> str:
    """A whole number as each language groups its digits."""
    s = f"{int(round(x)):,}"
    if lang in ("it", "es"):
        return s.replace(",", ".")
    if lang == "fr":
        return s.replace(",", NNBSP)
    return s


def pct(x, lang: str) -> str:
    """A share as a whole percentage: 48%, in French 48 %."""
    n = f"{x * 100:.0f}"
    return f"{n}{NBSP}%" if lang == "fr" else f"{n}%"


def decimal(s: str, lang: str) -> str:
    """A decimal written with a point, as each language writes it."""
    return s.replace(".", ",") if lang in ("it", "es", "fr") else s


def calls(n: int, lang: str) -> str:
    return {"es": f"{n} llamada" if n == 1 else f"{n} llamadas",
            "fr": f"{n} appel" if n == 1 else f"{n} appels",
            "zh": f"{n} 次调用"}[lang]


# -- labels (report.L) ------------------------------------------------------------------

LABELS: dict[str, dict] = {
    "es": {
        "title": "Telemetría de las salidas de las herramientas",
        "nav_overview": "Resumen", "nav_tokens": "Tokens", "nav_saving": "Ahorro", "nav_sugg": "Sugerencias",
        "nav_tools": "Herramientas", "nav_fields": "Campos", "nav_defs": "Definiciones", "nav_answers": "Respuestas",
        "nav_adaptive": "Aprendizaje", "nav_measures": "Medidas", "nav_config": "Configuración", "nav_about": "Información",
        "theme": "Tema: automático, claro, oscuro",
        "theme_auto": "Auto", "theme_light": "Claro", "theme_dark": "Oscuro",
        "status_on": "Filtro activo", "status_learning": "Aprendiendo", "status_offline": "Informe sin conexión",
        "mode": "Modo",
        "status_offline_text": "Generado con `tracetrim report` a partir de trazas grabadas: no se aplica nada.",
        "updated": "actualizado", "status_trimmed": "herramientas filtradas", "status_runs": "ejecuciones completas",
        "status_promoted": "campos promovidos",
        "config_json": "Configuración de ejecución (JSON)", "copy": "Copiar", "copied": "Copiado", "more": "más",
        "subtitle": "Lo que enviaron las herramientas, lo que usó el agente y qué cambiar.",
        "traces": "trazas", "calls": "llamadas a herramientas", "raw": "tokens devueltos por las herramientas",
        "prudent": "tras la proyección prudente", "aggressive": "tras la proyección agresiva",
        "misses": "fallos en ejecuciones excluidas", "suggestions": "Sugerencias", "none": "Ninguna sugerencia.",
        "auto_title": "Modo automático",
        "auto_applied": "el filtro está activo, con las acciones automáticas de abajo",
        "auto_learning": "aprendiendo: ejecuciones completas",
        "auto_off": "desactivadas tras demasiadas solicitudes de expand",
        "auto_refused": "cierres rechazados por falta de respuesta (la ejecución quedó abierta)",
        "mixed_runs": "advertencia: ejecuciones con llamadas de varias sesiones HTTP",
        "mixed_occurrences": "sesiones HTTP en esas ejecuciones (una vez por sesión y ejecución)",
        "auto_followed": "ejecuciones cerradas con una respuesta y seguidas de llamadas dentro de la pausa de inactividad "
                         "(ver la nota de abajo)",
        "savings": "De dónde viene el ahorro",
        "savings_sub": "Contado en tiempo de ejecución, llamada a llamada: lo que enviaron las herramientas frente a lo "
                       "que recibió el modelo. La reserialización es lo que se ahorra al escribir los mismos datos como "
                       "JSON compacto, sin quitar nada. Los campos son lo que la proyección quita además, descontada la "
                       "línea que enumera lo quitado. Ambos suman el total. Durante el aprendizaje no se quita ningún "
                       "campo: solo ahorra la reserialización. Lo devuelto por expand se cuenta aparte y no se resta del "
                       "ahorro.",
        "sent_by_tools": "enviados por las herramientas", "received_by_model": "recibidos por el modelo",
        "saving_total": "total", "saving_reserialisation": "reserialización", "saving_fields": "campos",
        "expand_back": "devueltos por expand (llamadas, tamaño)", "all_runs": "todas las ejecuciones",
        "savings_per_tool": "Por herramienta, todas las ejecuciones", "project": "Proyecto y autor",
        "hero": "de los tokens devueltos por las herramientas, quitados por la proyección prudente en ejecuciones que "
                "nunca vio",
        "misses_prudent": "fallos en ejecuciones excluidas, proyección prudente (la aplicada)",
        "hero_sub": "proyección agresiva",
        "hero_one": "de los tokens devueltos por las herramientas, ahorrados hasta ahora solo escribiendo JSON compacto, "
                    "sin quitar campos: para aprender y comprobar una proyección hacen falta al menos dos ejecuciones",
        "where": "Adónde van los tokens",
        "where_sub": "Tokens devueltos por cada herramienta y la parte que la proyección prudente quita en las "
                     "ejecuciones excluidas.",
        "kept": "llega al agente", "removed": "quitado", "others": "otras herramientas",
        "fields_bar": "tokens de los campos", "n_fields": "campos",
        "project_text": "Informe generado por una herramienta de código abierto que aprende, de las trazas de un agente, "
                        "qué campos de las salidas de las herramientas reutiliza visiblemente el agente.",
        "auto": "AUTOMÁTICA", "manual": "MANUAL", "actions": "Acciones automáticas",
        "config_text": "Las acciones automáticas se recogen en una configuración de ejecución, escrita junto a este "
                       "informe con la proyección que usa. Aplícala tal cual o edítala antes: es JSON simple.",
        "per_tool": "Por herramienta", "tool": "herramienta", "share": "cuota", "errors": "errores",
        "prose": "tokens de texto", "fields": "Campos", "field": "campo", "tokens": "tokens", "seen": "en llamadas",
        "used": "usado en", "status": "decisión", "missed": "fallado",
        "used_s": "conservado: usado", "key_s": "conservado: clave del registro",
        "unjudged_s": "conservado: no evaluable", "dropped_s": "quitado",
        "promoted_s": "conservado: promovido por evidencia",
        "adaptive": "Aprendizaje adaptativo",
        "adaptive_sub": "Lo que el agente volvió a pedir con expand, lo que usó cuando un resultado pasó entero y los "
                        "campos conservados por ello. La evidencia cuenta ejecuciones, no solicitudes. Un campo visto "
                        "entero y no visto reutilizado no está demostrado inútil: no se quita nada por eso.",
        "promotions": "Campos promovidos", "no_promotions": "Ningún campo promovido.",
        "resumed": "Campos recuperados con expand y luego conservados por el aprendizaje ordinario (el conteo de "
                   "desactivación de la herramienta volvió a empezar)",
        "promote_off": "la promoción está desactivada: la evidencia se registra, no se promueve nada",
        "reason": "motivo", "runs": "ejecuciones", "state": "estado",
        "reason_requested": "pedido por nombre", "reason_reused": "reutilizado tras recuperarlo",
        "reason_used_in_full": "usado en un resultado sin filtrar",
        "status_active": "activa", "status_reverted": "anulada",
        "effect": "Efecto de las promociones en los tokens (llamadas grabadas de la herramienta)",
        "without_p": "sin promociones", "with_p": "con promociones",
        "evidence": "Evidencia por campo (ejecuciones)",
        "ev_requested": "pedido", "ev_recovered": "recuperado", "ev_reused": "reutilizado tras recuperarlo",
        "ev_seen_full": "visto sin filtrar", "ev_used_in_full": "usado sin filtrar",
        "exposure": "Resultados filtrados y muestreados", "filtered": "filtrados", "expanded": "de ellos expandidos",
        "broad": "ejecuciones que piden todo", "sampled": "muestreados (pasados enteros)",
        "cheaper": "pasados enteros: el filtro no los habría acortado",
        "conflicts": "Por revisar: campos anulados que el aprendizaje ordinario conservaría (siguen quitados)",
        "compared_in": "las comparaciones en tiempo de ejecución están en", "eligible": "por filtrar",
        "removed_u": "quitado por el filtro", "added_u": "añadido por el muestreo",
        "lists": "Elementos de lista reutilizados (una medida: las filas nunca se cortan)",
        "list": "lista", "size_args": "tamaño pedido (argumento: mediana, máximo)",
        "received": "recibidos (mediana, máximo)", "reused_n": "reutilizados (mediana, máximo)",
        "from_top": "llamadas en que los reutilizados son los primeros",
        "deepest": "reutilizado más abajo (mediana, máximo)",
        "arguments": "Argumentos: valores distintos observados",
        "turns": "Llamadas por turno (una medida)",
        "turns_sub": "Por ejecución. Las llamadas que podían hacerse antes son una estimación: una llamada depende de un "
                     "resultado anterior solo si uno de sus argumentos es un identificador que ese resultado devolvió.",
        "run": "ejecución", "turns_n": "turnos", "fewest": "mínimo de turnos necesarios",
        "several": "turnos con varias llamadas", "could_earlier": "llamadas que podían hacerse antes",
        "argument": "argumento", "distinct": "valores distintos",
        "definitions": "Definiciones de las herramientas (una medida)",
        "defs_per_turn": "tokens de definiciones por turno", "defs_offered": "herramientas ofrecidas",
        "defs_called": "llamadas en al menos una ejecución completa", "defs_never": "nunca llamadas",
        "defs_never_tokens": "tokens de las herramientas nunca llamadas",
        "defs_own": "de las cuales herramientas de tracetrim",
        "defs_note": "Contados sobre el JSON de cada definición tal como se publica, con el tokenizador de este informe: "
                     "el proveedor factura su propia versión de las definiciones, normalmente algo menos. El modelo las "
                     "recibe en cada turno, así que una ejecución lleva esta cifra por sus turnos. tracetrim no ve los "
                     "turnos del modelo, así que aquí no se estima el peso por ejecución.",
        "defs_list": "Herramientas nunca llamadas, de la más pesada",
        "grounding": "Valores de las respuestas que ninguna herramienta devolvió",
        "gr_runs": "ejecuciones con respuesta comprobadas", "gr_values": "valores comprobados (números, códigos)",
        "gr_bad": "ejecuciones con un valor que ninguna salida contiene", "gr_run": "ejecución",
        "gr_found": "valores no encontrados", "gr_filtered": "filtradas en la ejecución",
        "gr_none": "Cada valor comprobado aparece en una salida de herramienta de su ejecución.",
        "gr_note": "Una comprobación determinista, sin modelo: números de tres o más cifras y códigos que los contienen, "
                   "buscados en cada salida de herramienta de la ejecución tal como la devolvió (con importes en "
                   "céntimos, fechas a partir de marcas de tiempo, números con separadores, porcentajes y redondeos de "
                   "un valor) y en los argumentos que escribió el agente. Los años no se comprueban, así que tampoco "
                   "ningún número de cuatro cifras entre 1900 y 2099. No ve un valor verdadero atribuido al elemento "
                   "equivocado; un valor calculado por el agente, o tomado de la petición del usuario, aparece en la "
                   "lista y puede ser correcto. En negrita, los valores de ejecuciones con resultados filtrados que no "
                   "faltan también en ejecuciones sin filtro: los únicos que pueden deberse al filtro.",
        "method": "Cómo leer este informe",
        "method_text": "Ahorros y fallos se miden dejando fuera una ejecución cada vez: cada ejecución se recorta con una "
                       "proyección aprendida de las demás. Un fallo es una llamada en la que la proyección agresiva quitó "
                       "un campo que la ejecución usó después. El uso es un límite inferior: un campo leído solo al "
                       "razonar no se ve. Los tokens de un campo son los de su fragmento, sumados en las llamadas. Las "
                       "sugerencias vienen de reglas fijas; los números están en report.json.",
        "level": {"warning": "atención", "risk": "riesgo", "saving": "ahorro", "quick-win": "mejora rápida",
                  "review": "por revisar", "limit": "límite"},
    },
    "fr": {
        "title": "Télémétrie des sorties d'outils",
        "nav_overview": "Vue d'ensemble", "nav_tokens": "Jetons", "nav_saving": "Économie", "nav_sugg": "Suggestions",
        "nav_tools": "Outils", "nav_fields": "Champs", "nav_defs": "Définitions", "nav_answers": "Réponses",
        "nav_adaptive": "Apprentissage", "nav_measures": "Mesures", "nav_config": "Configuration", "nav_about": "À propos",
        "theme": "Thème : automatique, clair, sombre",
        "theme_auto": "Auto", "theme_light": "Clair", "theme_dark": "Sombre",
        "status_on": "Filtrage actif", "status_learning": "Apprentissage", "status_offline": "Rapport hors ligne",
        "mode": "Mode",
        "status_offline_text": "Produit avec `tracetrim report` à partir de traces enregistrées : rien n'est appliqué.",
        "updated": "mis à jour", "status_trimmed": "outils filtrés", "status_runs": "exécutions complètes",
        "status_promoted": "champs promus",
        "config_json": "Configuration d'exécution (JSON)", "copy": "Copier", "copied": "Copié", "more": "de plus",
        "subtitle": "Ce que les outils ont envoyé, ce que l'agent a utilisé et ce qu'il faut changer.",
        "traces": "traces", "calls": "appels d'outils", "raw": "jetons renvoyés par les outils",
        "prudent": "après la projection prudente", "aggressive": "après la projection agressive",
        "misses": "pertes sur exécutions exclues", "suggestions": "Suggestions", "none": "Aucune suggestion.",
        "auto_title": "Mode automatique",
        "auto_applied": "le filtrage est actif, avec les actions automatiques ci-dessous",
        "auto_learning": "apprentissage : exécutions complètes",
        "auto_off": "désactivés après trop de demandes d'expand",
        "auto_refused": "clôtures refusées faute de réponse (l'exécution est restée ouverte)",
        "mixed_runs": "avertissement : exécutions avec des appels de plusieurs sessions HTTP",
        "mixed_occurrences": "sessions HTTP dans ces exécutions (une fois par session et par exécution)",
        "auto_followed": "exécutions closes par une réponse et suivies d'appels dans la pause d'inactivité (voir la note "
                         "ci-dessous)",
        "savings": "D'où vient l'économie",
        "savings_sub": "Compté à l'exécution, appel par appel : ce que les outils ont envoyé face à ce que le modèle a "
                       "reçu. La resérialisation est ce qu'on économise en écrivant les mêmes données en JSON compact, "
                       "sans rien retirer. Les champs sont ce que la projection retire en plus, déduction faite de la "
                       "ligne qui liste ce qui a été retiré. Les deux font le total. Pendant l'apprentissage, aucun champ "
                       "n'est retiré : seule la resérialisation économise. Ce qu'expand a rendu est compté à part et "
                       "n'est pas soustrait de l'économie.",
        "sent_by_tools": "envoyés par les outils", "received_by_model": "reçus par le modèle",
        "saving_total": "total", "saving_reserialisation": "resérialisation", "saving_fields": "champs",
        "expand_back": "rendus par expand (appels, taille)", "all_runs": "toutes les exécutions",
        "savings_per_tool": "Par outil, toutes les exécutions", "project": "Projet et auteur",
        "hero": "des jetons renvoyés par les outils, retirés par la projection prudente sur des exécutions jamais vues",
        "misses_prudent": "pertes sur exécutions exclues, projection prudente (celle appliquée)",
        "hero_sub": "projection agressive",
        "hero_one": "des jetons renvoyés par les outils, économisés jusqu'ici en écrivant seulement du JSON compact, sans "
                    "retirer de champ : apprendre et vérifier une projection demande au moins deux exécutions",
        "where": "Où vont les jetons",
        "where_sub": "Jetons renvoyés par chaque outil, et la part que la projection prudente retire sur les exécutions "
                     "exclues.",
        "kept": "parvient à l'agent", "removed": "retiré", "others": "autres outils",
        "fields_bar": "jetons des champs", "n_fields": "champs",
        "project_text": "Rapport produit par un outil open source qui apprend, à partir des traces d'un agent, quels champs "
                        "des sorties d'outils l'agent réutilise de façon visible.",
        "auto": "AUTOMATIQUE", "manual": "MANUELLE", "actions": "Actions automatiques",
        "config_text": "Les actions automatiques sont réunies dans une configuration d'exécution, écrite à côté de ce "
                       "rapport avec la projection qu'elle utilise. Appliquez-la telle quelle ou modifiez-la d'abord : "
                       "c'est du JSON simple.",
        "per_tool": "Par outil", "tool": "outil", "share": "part", "errors": "erreurs", "prose": "jetons de texte",
        "fields": "Champs", "field": "champ", "tokens": "jetons", "seen": "dans les appels", "used": "utilisé dans",
        "status": "décision", "missed": "perdu",
        "used_s": "conservé : utilisé", "key_s": "conservé : clé de l'enregistrement",
        "unjudged_s": "conservé : non évaluable", "dropped_s": "retiré",
        "promoted_s": "conservé : promu sur preuve",
        "adaptive": "Apprentissage adaptatif",
        "adaptive_sub": "Ce que l'agent a redemandé avec expand, ce qu'il a utilisé quand un résultat est passé en entier, "
                        "et les champs conservés en conséquence. Les preuves comptent des exécutions, pas des demandes. "
                        "Un champ vu en entier et non réutilisé n'est pas prouvé inutile : rien n'est retiré pour cela.",
        "promotions": "Champs promus", "no_promotions": "Aucun champ promu.",
        "resumed": "Champs récupérés avec expand puis conservés par l'apprentissage ordinaire (le compte de désactivation "
                   "de l'outil est reparti de zéro)",
        "promote_off": "la promotion est désactivée : les preuves sont enregistrées, rien n'est promu",
        "reason": "motif", "runs": "exécutions", "state": "état",
        "reason_requested": "demandé par son nom", "reason_reused": "réutilisé après récupération",
        "reason_used_in_full": "utilisé dans un résultat non filtré",
        "status_active": "active", "status_reverted": "annulée",
        "effect": "Effet des promotions sur les jetons (appels enregistrés de l'outil)",
        "without_p": "sans promotions", "with_p": "avec promotions",
        "evidence": "Preuves par champ (exécutions)",
        "ev_requested": "demandé", "ev_recovered": "récupéré", "ev_reused": "réutilisé après récupération",
        "ev_seen_full": "vu non filtré", "ev_used_in_full": "utilisé non filtré",
        "exposure": "Résultats filtrés et échantillonnés", "filtered": "filtrés", "expanded": "dont étendus",
        "broad": "exécutions demandant tout", "sampled": "échantillonnés (passés en entier)",
        "cheaper": "passés en entier : le filtrage ne les aurait pas raccourcis",
        "conflicts": "À revoir : champs annulés que l'apprentissage ordinaire conserverait (ils restent retirés)",
        "compared_in": "les comparaisons à l'exécution sont en", "eligible": "à filtrer",
        "removed_u": "retiré par le filtrage", "added_u": "ajouté par l'échantillonnage",
        "lists": "Éléments de liste réutilisés (une mesure : les lignes ne sont jamais coupées)",
        "list": "liste", "size_args": "taille demandée (argument : médiane, maximum)",
        "received": "reçus (médiane, maximum)", "reused_n": "réutilisés (médiane, maximum)",
        "from_top": "appels où les réutilisés sont les premiers",
        "deepest": "réutilisé le plus bas (médiane, maximum)",
        "arguments": "Arguments : valeurs distinctes observées",
        "turns": "Appels par tour (une mesure)",
        "turns_sub": "Par exécution. Les appels qui pouvaient partir plus tôt sont une estimation : un appel ne dépend "
                     "d'un résultat antérieur que si l'un de ses arguments est un identifiant renvoyé par ce résultat.",
        "run": "exécution", "turns_n": "tours", "fewest": "minimum de tours nécessaires",
        "several": "tours avec plusieurs appels", "could_earlier": "appels qui pouvaient partir plus tôt",
        "argument": "argument", "distinct": "valeurs distinctes",
        "definitions": "Définitions des outils (une mesure)",
        "defs_per_turn": "jetons de définitions par tour", "defs_offered": "outils proposés",
        "defs_called": "appelés dans au moins une exécution complète", "defs_never": "jamais appelés",
        "defs_never_tokens": "jetons des outils jamais appelés", "defs_own": "dont outils de tracetrim",
        "defs_note": "Comptés sur le JSON de chaque définition tel que publié, avec le tokenizer de ce rapport : le "
                     "fournisseur facture sa propre version des définitions, en général un peu moins. Le modèle les "
                     "reçoit à chaque tour, donc une exécution porte ce chiffre multiplié par ses tours. tracetrim ne "
                     "voit pas les tours du modèle : le poids par exécution n'est pas estimé ici.",
        "defs_list": "Outils jamais appelés, du plus lourd au plus léger",
        "grounding": "Valeurs des réponses qu'aucun outil n'a renvoyées",
        "gr_runs": "exécutions avec réponse vérifiées", "gr_values": "valeurs vérifiées (nombres, codes)",
        "gr_bad": "exécutions avec une valeur qu'aucune sortie ne contient", "gr_run": "exécution",
        "gr_found": "valeurs non trouvées", "gr_filtered": "filtrés dans l'exécution",
        "gr_none": "Chaque valeur vérifiée figure dans une sortie d'outil de son exécution.",
        "gr_note": "Une vérification déterministe, sans modèle : nombres de trois chiffres ou plus et codes qui en "
                   "contiennent, cherchés dans chaque sortie d'outil de l'exécution telle que l'outil l'a renvoyée "
                   "(montants en centimes, dates issues d'horodatages, nombres avec séparateurs, pourcentages et "
                   "arrondis d'une valeur compris) et dans les arguments écrits par l'agent. Les années ne sont pas "
                   "vérifiées, ni donc aucun nombre de quatre chiffres entre 1900 et 2099. Elle ne voit pas une valeur "
                   "vraie attribuée au mauvais élément ; une valeur calculée par l'agent, ou tirée de la demande de "
                   "l'utilisateur, est listée et peut être juste. En gras, les valeurs d'exécutions aux résultats "
                   "filtrés qui ne manquent pas aussi dans des exécutions sans filtrage : les seules que le filtrage "
                   "peut expliquer.",
        "method": "Comment lire ce rapport",
        "method_text": "Économies et pertes sont mesurées en laissant de côté une exécution à la fois : chaque exécution "
                       "est filtrée avec une projection apprise sur les autres. Une perte est un appel où la projection "
                       "agressive a retiré un champ que l'exécution a utilisé ensuite. L'usage est une borne inférieure : "
                       "un champ lu seulement pendant le raisonnement est invisible. Les jetons d'un champ sont ceux de "
                       "son fragment, additionnés sur les appels. Les suggestions viennent de règles fixes ; les nombres "
                       "sont dans report.json.",
        "level": {"warning": "attention", "risk": "risque", "saving": "économie", "quick-win": "gain rapide",
                  "review": "à revoir", "limit": "limite"},
    },
    "zh": {
        "title": "工具输出遥测",
        "nav_overview": "概览", "nav_tokens": "令牌", "nav_saving": "节省", "nav_sugg": "建议", "nav_tools": "工具",
        "nav_fields": "字段", "nav_defs": "定义", "nav_answers": "回答", "nav_adaptive": "学习", "nav_measures": "度量",
        "nav_config": "配置", "nav_about": "关于",
        "theme": "主题：自动、浅色、深色", "theme_auto": "自动", "theme_light": "浅色", "theme_dark": "深色",
        "status_on": "过滤已开启", "status_learning": "学习中", "status_offline": "离线报告", "mode": "模式",
        "status_offline_text": "由 `tracetrim report` 根据录制的轨迹生成：不会应用任何内容。",
        "updated": "更新于", "status_trimmed": "已过滤的工具", "status_runs": "完整运行", "status_promoted": "已提升的字段",
        "config_json": "运行时配置（JSON）", "copy": "复制", "copied": "已复制", "more": "更多",
        "subtitle": "工具发送了什么、代理用了什么，以及该改什么。",
        "traces": "轨迹", "calls": "工具调用", "raw": "工具返回的令牌",
        "prudent": "谨慎投影之后", "aggressive": "激进投影之后", "misses": "留出运行中的遗漏",
        "suggestions": "建议", "none": "没有建议。",
        "auto_title": "自动模式",
        "auto_applied": "过滤已开启，并执行下方的自动操作",
        "auto_learning": "学习中：完整运行",
        "auto_off": "因 expand 请求过多而关闭",
        "auto_refused": "因缺少回答而被拒绝的关闭（运行仍保持打开）",
        "mixed_runs": "警告：包含多个 HTTP 会话调用的运行次数",
        "mixed_occurrences": "这些运行中的 HTTP 会话数（每次运行中每个会话计一次）",
        "auto_followed": "以回答结束、并在空闲间隔内又出现调用的运行（见下方说明）",
        "savings": "节省从何而来",
        "savings_sub": "在运行时逐次调用统计：工具发送的内容与模型收到的内容之比。重新序列化是把同样的数据写成紧凑 JSON "
                       "所节省的部分，不删除任何内容。字段是投影在此基础上额外删除的部分，已扣除列出被删除内容的那一行。"
                       "两者相加即为总计。学习期间不删除任何字段：只有重新序列化在节省。expand 返回的内容单独计算，不从节省中扣除。",
        "sent_by_tools": "工具发送", "received_by_model": "模型收到", "saving_total": "总计",
        "saving_reserialisation": "重新序列化", "saving_fields": "字段",
        "expand_back": "expand 返回（调用次数，大小）", "all_runs": "全部运行",
        "savings_per_tool": "按工具，全部运行", "project": "项目与作者",
        "hero": "是工具返回令牌中被谨慎投影删除的比例（在它从未见过的运行上）",
        "misses_prudent": "留出运行中的遗漏，谨慎投影（实际应用的那个）",
        "hero_sub": "激进投影",
        "hero_one": "是工具返回令牌中目前仅靠写成紧凑 JSON 节省的比例，未删除任何字段：学习并验证一个投影至少需要两次运行",
        "where": "令牌去了哪里", "where_sub": "每个工具返回的令牌，以及谨慎投影在留出运行上删除的部分。",
        "kept": "到达代理", "removed": "已删除", "others": "其他工具",
        "fields_bar": "字段令牌", "n_fields": "个字段",
        "project_text": "本报告由一个开源工具生成，它从代理的轨迹中学习代理明显复用了工具输出中的哪些字段。",
        "auto": "自动", "manual": "手动", "actions": "自动操作",
        "config_text": "自动操作汇总在一个运行时配置中，与它所用的投影一起写在本报告旁边。可直接应用，也可先编辑：它就是普通的 JSON。",
        "per_tool": "按工具", "tool": "工具", "share": "占比", "errors": "错误", "prose": "文本令牌",
        "fields": "字段", "field": "字段", "tokens": "令牌", "seen": "出现于调用", "used": "使用于",
        "status": "决定", "missed": "遗漏",
        "used_s": "保留：已使用", "key_s": "保留：记录键", "unjudged_s": "保留：无法判断", "dropped_s": "已删除",
        "promoted_s": "保留：依据证据提升",
        "adaptive": "自适应学习",
        "adaptive_sub": "代理用 expand 要回了什么、结果整体传递时它用了什么，以及因此保留的字段。证据按运行计数，不按请求计数。"
                        "一个被整体看到却未被复用的字段并不能证明无用：不会因此删除任何内容。",
        "promotions": "已提升的字段", "no_promotions": "没有字段被提升。",
        "resumed": "用 expand 找回、随后被常规学习保留的字段（该工具的关闭计数已重新开始）",
        "promote_off": "提升已关闭：证据会被记录，但不会提升任何字段",
        "reason": "原因", "runs": "运行", "state": "状态",
        "reason_requested": "按名称请求", "reason_reused": "找回后复用", "reason_used_in_full": "在未过滤的结果中使用",
        "status_active": "生效", "status_reverted": "已撤销",
        "effect": "提升对令牌的影响（该工具已录制的调用）", "without_p": "不含提升", "with_p": "含提升",
        "evidence": "按字段的证据（运行）",
        "ev_requested": "已请求", "ev_recovered": "已找回", "ev_reused": "找回后复用",
        "ev_seen_full": "未过滤时看到", "ev_used_in_full": "未过滤时使用",
        "exposure": "已过滤与抽样的结果", "filtered": "已过滤", "expanded": "其中被展开",
        "broad": "请求全部内容的运行", "sampled": "抽样（整体传递）",
        "cheaper": "整体传递：过滤并不会让它们更短",
        "conflicts": "待复查：已撤销但常规学习会保留的字段（仍保持删除）",
        "compared_in": "运行时比较的单位为", "eligible": "将被过滤",
        "removed_u": "过滤删除", "added_u": "抽样增加",
        "lists": "被复用的列表元素（一项度量：从不截断行）",
        "list": "列表", "size_args": "请求的大小（参数：中位数，最大值）",
        "received": "收到（中位数，最大值）", "reused_n": "复用（中位数，最大值）",
        "from_top": "复用的元素恰为最前面几项的调用", "deepest": "最靠后的复用位置（中位数，最大值）",
        "arguments": "参数：观察到的不同取值",
        "turns": "每轮的工具调用（一项度量）",
        "turns_sub": "按运行。可以更早发出的调用是一个估计：只有当某个参数是先前结果返回的标识符时，才认为该调用依赖于那个结果。",
        "run": "运行", "turns_n": "轮次", "fewest": "所需最少轮次", "several": "含多次调用的轮次",
        "could_earlier": "可以更早发出的调用", "argument": "参数", "distinct": "不同取值",
        "definitions": "工具定义（一项度量）",
        "defs_per_turn": "每轮的定义令牌", "defs_offered": "提供的工具", "defs_called": "至少在一次完整运行中被调用",
        "defs_never": "从未调用", "defs_never_tokens": "从未调用的工具的令牌", "defs_own": "其中 tracetrim 自带的工具",
        "defs_note": "按每个定义发布时的 JSON、用本报告的分词器计数：提供方按其自己的定义呈现方式计费，通常略少。"
                     "模型每一轮都会收到这些定义，因此一次运行承担的是该数值乘以其轮次。tracetrim 看不到模型的轮次，所以这里不估算每次运行的分量。",
        "defs_list": "从未调用的工具，按分量从大到小",
        "grounding": "没有任何工具返回过的回答中的值",
        "gr_runs": "已检查的带回答的运行", "gr_values": "已检查的值（数字、代码）",
        "gr_bad": "含有任何输出都不包含的值的运行", "gr_run": "运行", "gr_found": "未找到的值",
        "gr_filtered": "该运行中被过滤的工具",
        "gr_none": "每个被检查的值都出现在其运行的某个工具输出中。",
        "gr_note": "一项确定性的检查，不使用模型：三位及以上的数字以及包含它们的代码，会在该运行每个工具的原始输出中查找"
                   "（包括以分为单位的金额、由时间戳转换的日期、带分隔符的数字，以及某个值的百分比和四舍五入），"
                   "也会在代理写下的参数中查找。年份不检查，因此 1900 到 2099 之间的四位数也不检查。"
                   "它看不到被归到错误条目上的真实值；代理计算出的值，或来自用户请求的值，会被列出，但可能是正确的。"
                   "加粗的是在过滤过的运行中缺失、而在未过滤的运行中并不缺失的值：只有这些可能由过滤造成。",
        "method": "如何阅读本报告",
        "method_text": "节省和遗漏采用留一法测量：每次运行都用从其他运行学到的投影来裁剪。遗漏是指激进投影删除了该运行后来"
                       "用到的字段的调用。使用情况是一个下界：只在推理中读取的字段是看不见的。字段令牌是每个字段片段的令牌，"
                       "按调用累加。建议来自固定规则；数字见 report.json。",
        "level": {"warning": "注意", "risk": "风险", "saving": "节省", "quick-win": "快速改进", "review": "待复查",
                  "limit": "限制"},
    },
}

# -- actions (report.ACTION_TEXT) -------------------------------------------------------

ACTIONS: dict[str, dict[str, str]] = {
    "es": {
        "trim": "La configuración generada aplica la proyección prudente a esta herramienta.",
        "expand": "La configuración generada activa `expand` para esta herramienta, así un campo oculto se puede recuperar.",
        "unescape": "La configuración generada reserializa cada salida JSON en UTF-8.",
        "cache": "Si estos resultados no cambian dentro de una ejecución, guárdalos en caché en el agente o en la "
                 "herramienta: tracetrim no lo hace.",
        "fix_tool_description": "Reescribe la descripción de la herramienta con la restricción que revelan estos errores:",
        "review_fields": "Revisa estos campos y añade a la lista de campos quitados de la proyección los que la tarea no "
                         "necesita:",
        "limit_text_output": "Reduce cuántas veces se llama a esta herramienta, o cuánto texto devuelve.",
        "skill_strategy": "Prueba el nivel de estrategia de la skill y compara los turnos: `--skill-strategy` en el "
                          "proxy, `AutoTrim(strategy=True)` con function calling, `tracetrim skill --strategy` para "
                          "imprimir el texto. tracetrim nunca la activa por sí solo.",
        "unused_tools": "Herramientas nunca llamadas, con los tokens de su definición. Desactiva en el host las que este "
                        "agente no necesita; tracetrim no lo hace:",
        "check_values": "Relee estas respuestas comparándolas con las salidas de las herramientas de su ejecución. Si un "
                        "valor fue inventado, deja de filtrar la herramienta de la que debería venir (quítala de la "
                        "configuración) o conserva ese campo: tracetrim no cambia nada por sí solo:",
        "collect_traces": "Reúne más ejecuciones de la misma tarea y vuelve a generar el informe.",
    },
    "fr": {
        "trim": "La configuration générée applique la projection prudente à cet outil.",
        "expand": "La configuration générée active `expand` pour cet outil : un champ masqué peut être récupéré.",
        "unescape": "La configuration générée resérialise chaque sortie JSON en UTF-8.",
        "cache": "Si ces résultats ne changent pas au cours d'une exécution, mettez-les en cache dans l'agent ou l'outil : "
                 "tracetrim ne le fait pas.",
        "fix_tool_description": "Réécrivez la description de l'outil avec la contrainte que révèlent ces erreurs :",
        "review_fields": "Revoyez ces champs et ajoutez à la liste des champs retirés de la projection ceux dont la tâche "
                         "n'a pas besoin :",
        "limit_text_output": "Réduisez le nombre d'appels à cet outil, ou la quantité de texte qu'il renvoie.",
        "skill_strategy": "Essayez le niveau stratégie de la skill et comparez les tours : `--skill-strategy` sur le "
                          "proxy, `AutoTrim(strategy=True)` avec le function calling, `tracetrim skill --strategy` pour "
                          "afficher le texte. tracetrim ne l'active jamais de lui-même.",
        "unused_tools": "Outils jamais appelés, avec les jetons de leur définition. Désactivez dans l'hôte ceux dont cet "
                        "agent n'a pas besoin ; tracetrim ne le fait pas :",
        "check_values": "Relisez ces réponses en les comparant aux sorties d'outils de leur exécution. Si une valeur a été "
                        "inventée, cessez de filtrer l'outil dont elle aurait dû venir (retirez-le de la configuration) "
                        "ou conservez ce champ : tracetrim ne change rien de lui-même :",
        "collect_traces": "Rassemblez d'autres exécutions de la même tâche, puis générez à nouveau le rapport.",
    },
    "zh": {
        "trim": "生成的配置对该工具应用谨慎投影。",
        "expand": "生成的配置为该工具开启 `expand`，被隐藏的字段可以取回。",
        "unescape": "生成的配置把每个 JSON 输出重新序列化为 UTF-8。",
        "cache": "如果这些结果在一次运行中不会变化，请在代理或工具中缓存它们：tracetrim 不会这样做。",
        "fix_tool_description": "根据这些错误揭示的约束，重写该工具的描述：",
        "review_fields": "复查这些字段，把任务不需要的字段加入投影的删除列表：",
        "limit_text_output": "减少调用该工具的次数，或减少它返回的文本量。",
        "skill_strategy": "试试 skill 的策略层级并比较轮次：代理上用 `--skill-strategy`，function calling 用 "
                          "`AutoTrim(strategy=True)`，用 `tracetrim skill --strategy` 打印文本。tracetrim 从不自行开启它。",
        "unused_tools": "从未调用的工具及其定义的令牌。请在宿主中关闭此代理不需要的工具；tracetrim 不会这样做：",
        "check_values": "对照其运行的工具输出重读这些回答。如果某个值是编造的，请停止过滤它本应来自的工具（从配置中移除），"
                        "或保留该字段：tracetrim 不会自行更改任何内容：",
        "collect_traces": "收集同一任务的更多运行，然后重新生成报告。",
    },
}


def label(key: str, lang: str, sub: str | None = None) -> str | None:
    v = LABELS.get(lang, {}).get(key)
    if sub is not None:
        v = v.get(sub) if isinstance(v, dict) else None
    return v


# -- suggestions (report.suggestions): one template per rule and language -----------------

def suggestion(rule: str, lang: str, v: dict) -> str:
    """The text of a suggestion in Spanish, French or Chinese, from the values the rule computed."""
    n, p = (lambda x: num(x, lang)), (lambda x: pct(x, lang))
    code = lambda xs: ", ".join(f"`{x}`" for x in xs)          # noqa: E731
    es, fr, zh = lang == "es", lang == "fr", lang == "zh"
    if rule == "few-traces":
        t = v["traces"]
        return (f"Solo {t} traza{'' if t == 1 else 's'}: las proyecciones aprendidas son inestables. Reúne al menos "
                f"{v['min']}-10 ejecuciones de la misma tarea antes de aplicar una." if es else
                f"Seulement {t} trace{'' if t == 1 else 's'} : les projections apprises sont instables. Rassemblez au "
                f"moins {v['min']}-10 exécutions de la même tâche avant d'en appliquer une." if fr else
                f"只有 {t} 条轨迹：学到的投影不稳定。应用之前，请至少收集同一任务的 {v['min']}-10 次运行。")
    if rule == "escapes":
        return (f"Las salidas de las herramientas están indentadas o contienen escapes ASCII (\\uXXXX). Escribirlas "
                f"como JSON UTF-8 compacto ahorra {n(v['tokens'])} tokens ({p(v['share'])}), sin pérdida de información."
                if es else
                f"Les sorties d'outils sont indentées ou contiennent des échappements ASCII (\\uXXXX). Les écrire en JSON "
                f"UTF-8 compact économise {n(v['tokens'])} jetons ({p(v['share'])}), sans perte d'information." if fr else
                f"工具输出带有缩进或包含 ASCII 转义（\\uXXXX）。改写为紧凑的 UTF-8 JSON 可节省 {n(v['tokens'])} 个令牌"
                f"（{p(v['share'])}），不丢失任何信息。")
    if rule == "duplicates":
        d = v["duplicates"]
        return (f"{calls(d, lang)} {'repite' if d == 1 else 'repiten'} una llamada anterior de la misma ejecución con "
                f"argumentos idénticos. Guarda en caché los resultados de las herramientas dentro de una ejecución." if es else
                f"{calls(d, lang)} {'répète' if d == 1 else 'répètent'} un appel précédent de la même exécution avec des "
                f"arguments identiques. Mettez en cache les résultats des outils au sein d'une exécution." if fr else
                f"有 {calls(d, lang)}重复了同一运行中参数完全相同的先前调用。请在一次运行内缓存工具结果。")
    if rule == "trim":
        return (f"`{v['tool']}` lleva {p(v['share'])} de todos los tokens devueltos por las herramientas. En las "
                f"ejecuciones de las que no aprendió, la proyección prudente quita {p(v['saving'])} "
                f"({n(v['tokens'])} tokens en {calls(v['calls'], lang)})." if es else
                f"`{v['tool']}` porte {p(v['share'])} de tous les jetons renvoyés par les outils. Sur les exécutions "
                f"dont elle n'a pas appris, la projection prudente en retire {p(v['saving'])} "
                f"({n(v['tokens'])} jetons sur {calls(v['calls'], lang)})." if fr else
                f"`{v['tool']}` 占全部工具返回令牌的 {p(v['share'])}。在投影未从中学习的运行上，谨慎投影删除了其中的 "
                f"{p(v['saving'])}（{calls(v['calls'], lang)}共 {n(v['tokens'])} 个令牌）。")
    if rule == "misses":
        return (f"La proyección agresiva de `{v['tool']}` ocultó un campo que un paso posterior usó en "
                f"{calls(v['misses'], lang)} de {v['trimmed']} ({code(v['fields'])}). Usa la proyección prudente, "
                f"activa `expand` y reúne más trazas antes de recortar de forma agresiva." if es else
                f"La projection agressive de `{v['tool']}` a masqué un champ utilisé plus loin dans "
                f"{calls(v['misses'], lang)} sur {v['trimmed']} ({code(v['fields'])}). Utilisez la projection prudente, "
                f"activez `expand` et rassemblez plus de traces avant de filtrer de façon agressive." if fr else
                f"`{v['tool']}` 的激进投影在 {v['trimmed']} 次留出调用中有 {calls(v['misses'], lang)}隐藏了后续步骤用到的字段"
                f"（{code(v['fields'])}）。请使用谨慎投影、开启 `expand`，并在激进裁剪之前收集更多轨迹。")
    if rule == "prose":
        return (f"`{v['tool']}` devuelve texto, no datos estructurados: {n(v['tokens'])} tokens ({p(v['share'])} de toda "
                f"la salida de las herramientas). La proyección de campos no puede reducirlo: llámala menos, pide salidas "
                f"más cortas o usa un compresor de texto." if es else
                f"`{v['tool']}` renvoie du texte, pas des données structurées : {n(v['tokens'])} jetons ({p(v['share'])} de "
                f"toute la sortie des outils). La projection des champs ne peut pas le réduire : appelez-le moins souvent, "
                f"demandez des sorties plus courtes ou utilisez un compresseur de texte." if fr else
                f"`{v['tool']}` 返回的是文本而不是结构化数据：{n(v['tokens'])} 个令牌（占全部工具输出的 {p(v['share'])}）。"
                f"字段投影无法减少它：请减少调用次数、要求更短的输出，或使用文本压缩器。")
    if rule == "unjudged":
        return (f"En `{v['tool']}`, {p(v['share'])} de los tokens de los campos pertenece a campos que la detección de uso "
                f"no puede evaluar (números pequeños, booleanos, etiquetas cortas), p. ej. {code(v['fields'])}. La "
                f"proyección prudente los conserva: revísalos y quita explícitamente los que la tarea no necesita." if es else
                f"Dans `{v['tool']}`, {p(v['share'])} des jetons des champs appartiennent à des champs que la détection "
                f"d'usage ne sait pas évaluer (petits nombres, booléens, libellés courts), par ex. {code(v['fields'])}. "
                f"La projection prudente les conserve : revoyez-les et retirez explicitement ceux dont la tâche n'a pas "
                f"besoin." if fr else
                f"在 `{v['tool']}` 中，字段令牌的 {p(v['share'])} 属于使用检测无法判断的字段（小数字、布尔值、短标签），"
                f"例如 {code(v['fields'])}。谨慎投影会保留它们：请复查，并明确删除任务不需要的字段。")
    if rule == "errors":
        return (f"{calls(v['errors'], lang)} de {v['calls']} a `{v['tool']}` {'devolvió' if v['errors'] == 1 else 'devolvieron'} "
                f"un error. Cada una cuesta un turno entero: revisa los argumentos que envía el agente y escribe la "
                f"restricción en la descripción de la herramienta." if es else
                f"{calls(v['errors'], lang)} sur {v['calls']} à `{v['tool']}` {'a' if v['errors'] == 1 else 'ont'} renvoyé "
                f"une erreur. Chacun coûte un tour entier : vérifiez les arguments que l'agent envoie et écrivez la "
                f"contrainte dans la description de l'outil." if fr else
                f"对 `{v['tool']}` 的 {v['calls']} 次调用中有 {calls(v['errors'], lang)}返回了错误。每一次都要多花一整轮："
                f"请检查代理发送的参数，并把约束写进工具描述。")
    if rule == "strategy":
        return (f"En las {v['runs']} ejecuciones grabadas el agente hace casi siempre una llamada por turno "
                f"({p(v['several'])} de los turnos con llamadas hace varias), y cerca de {p(v['earlier'])} de sus llamadas "
                f"tenía los argumentos disponibles antes del turno en que se hizo (una estimación: solo identificadores). "
                f"El nivel de estrategia de la skill de tracetrim podría reducir los turnos: en un modelo que trabaja así "
                f"llevó una tarea de una mediana de 17 turnos a 6 (`docs/measures-v04.md`, E3); en un modelo que ya agrupa "
                f"sus llamadas no cambió nada." if es else
                f"Dans les {v['runs']} exécutions enregistrées, l'agent fait presque toujours un appel par tour "
                f"({p(v['several'])} des tours avec appels en font plusieurs), et environ {p(v['earlier'])} de ses appels "
                f"avaient leurs arguments disponibles avant le tour où ils sont partis (une estimation : identifiants "
                f"seulement). Le niveau stratégie de la skill de tracetrim pourrait réduire les tours : sur un modèle qui "
                f"travaille ainsi, il a fait passer une tâche d'une médiane de 17 tours à 6 (`docs/measures-v04.md`, E3) ; "
                f"sur un modèle qui regroupe déjà ses appels, il n'a rien changé." if fr else
                f"在录制的 {v['runs']} 次运行中，代理几乎总是每轮只发一次调用（有调用的轮次中只有 {p(v['several'])} 发出多次调用），"
                f"而约 {p(v['earlier'])} 的调用在发出它的那一轮之前就已具备参数（估计值：仅限标识符）。tracetrim skill 的策略层级"
                f"可能减少轮次：在一个这样工作的模型上，它把一个任务的轮次中位数从 17 降到 6（`docs/measures-v04.md`，E3）；"
                f"在已经会合并调用的模型上则没有变化。")
    if rule == "definitions":
        k, total, runs, shown = v["never"], v["offered"], v["runs"], v["shown"]
        if es:
            listed = f"las primeras {shown} de {k}, abajo" if k > shown else "abajo"
            return (f"Las definiciones de las herramientas pesan {n(v['tokens_per_turn'])} tokens, y el modelo las recibe "
                    f"en cada turno. {k} de las {total} herramientas nunca se llamaron en {runs} ejecuciones completas y "
                    f"pesan {n(v['never_tokens'])} de esos tokens ({p(v['never_share'])}); de la más pesada, {listed}. Si "
                    f"este agente no las necesita, desactívalas en el host; o usa la búsqueda nativa de herramientas donde "
                    f"el host o la API la tengan, que carga una definición solo cuando hace falta (Claude Code: activa por "
                    f"defecto para las herramientas MCP; Responses API de OpenAI: `tool_search` con `defer_loading`). "
                    f"«Nunca llamada en {runs} ejecuciones» no significa «nunca necesaria»: otra petición puede necesitar "
                    f"alguna. tracetrim no oculta ninguna herramienta por sí solo.")
        if fr:
            listed = f"les {shown} premiers sur {k} ci-dessous" if k > shown else "ci-dessous"
            return (f"Les définitions des outils pèsent {n(v['tokens_per_turn'])} jetons, et le modèle les reçoit à chaque "
                    f"tour. {k} des {total} outils n'ont jamais été appelés en {runs} exécutions complètes et pèsent "
                    f"{n(v['never_tokens'])} de ces jetons ({p(v['never_share'])}) ; du plus lourd, {listed}. Si cet agent "
                    f"n'en a pas besoin, désactivez-les dans l'hôte ; ou utilisez la recherche native d'outils là où l'hôte "
                    f"ou l'API l'offre, qui ne charge une définition qu'au besoin (Claude Code : active par défaut pour les "
                    f"outils MCP ; Responses API d'OpenAI : `tool_search` avec `defer_loading`). « Jamais appelé en {runs} "
                    f"exécutions » ne veut pas dire « jamais nécessaire » : une autre demande peut en avoir besoin. "
                    f"tracetrim ne masque aucun outil de lui-même.")
        listed = f"下方列出其中最重的 {shown} 个（共 {k} 个）" if k > shown else "见下方，按分量从大到小"
        return (f"工具定义共 {n(v['tokens_per_turn'])} 个令牌，模型每一轮都会收到。{total} 个工具中有 {k} 个在 {runs} 次完整运行中"
                f"从未被调用，占其中 {n(v['never_tokens'])} 个令牌（{p(v['never_share'])}）；{listed}。如果此代理不需要它们，"
                f"请在宿主中关闭；或在宿主或 API 支持时使用原生工具搜索，它只在需要时加载定义（Claude Code：对 MCP 工具默认开启；"
                f"OpenAI Responses API：`tool_search` 配合 `defer_loading`）。“{runs} 次运行中从未调用”并不等于“从不需要”："
                f"其他请求可能会用到。tracetrim 不会自行隐藏任何工具。")
    if rule == "grounding":
        bad, checked, k, tools = v["bad"], v["checked"], v["values"], code(v["tools"])
        return (f"En {bad} de {checked} ejecuciones con respuesta, la respuesta cita {k} valor{'' if k == 1 else 'es'} "
                f"(números, códigos) que ninguna salida de herramienta de su ejecución contiene, mientras los resultados "
                f"de {tools} llegaban filtrados al modelo. Un agente que ya no ve un campo puede inventarlo: revisa esas "
                f"respuestas. Un valor también puede ser un cálculo del agente o venir de la petición del usuario, que "
                f"tracetrim no ve; los valores que faltan también en ejecuciones sin filtro no se cuentan aquí." if es else
                f"Dans {bad} exécutions avec réponse sur {checked}, la réponse cite {k} valeur{'' if k == 1 else 's'} "
                f"(nombres, codes) qu'aucune sortie d'outil de son exécution ne contient, alors que les résultats de "
                f"{tools} parvenaient filtrés au modèle. Un agent qui ne voit plus un champ peut l'inventer : vérifiez ces "
                f"réponses. Une valeur peut aussi être un calcul de l'agent, ou venir de la demande de l'utilisateur, que "
                f"tracetrim ne voit pas ; les valeurs qui manquent aussi dans des exécutions sans filtrage ne sont pas "
                f"comptées ici." if fr else
                f"在 {checked} 次带回答的运行中，有 {bad} 次的回答引用了 {k} 个值（数字、代码），而该运行的任何工具输出都不包含这些值；"
                f"同时 {tools} 的结果是经过过滤后才到达模型的。一个看不到某字段的代理可能会编造它：请检查这些回答。"
                f"某个值也可能是代理自己计算的，或来自 tracetrim 看不到的用户请求；在未过滤运行中同样缺失的值不计入此处。")
    raise KeyError(rule)


def followed(n: int, pause: float | None, lang: str) -> str:
    """The note on runs closed by an answer and followed by calls (report._followed_note)."""
    if lang == "es":
        p = f" ({pause:g} segundos)" if pause else ""
        return (f"Ejecuciones cerradas con una respuesta y seguidas de llamadas: {n}. Este conteo suma dos casos que "
                f"tracetrim no puede distinguir: el agente cerró la ejecución demasiado pronto y siguió con la misma "
                f"tarea, o la tarea siguiente empezó dentro de la pausa de inactividad{p}. En un host donde el usuario "
                f"abre enseguida la conversación siguiente, crece en cada ejecución: por sí solo no indica un problema. "
                f"En ambos casos las ejecuciones quedan separadas, nunca se fusionan.")
    if lang == "fr":
        p = f" ({pause:g} secondes)" if pause else ""
        return (f"Exécutions closes par une réponse et suivies d'appels : {n}. Ce compte additionne deux cas que "
                f"tracetrim ne sait pas distinguer : l'agent a clos l'exécution trop tôt et a poursuivi la même tâche, ou "
                f"la tâche suivante a commencé dans la pause d'inactivité{p}. Avec un hôte où l'utilisateur ouvre aussitôt "
                f"la conversation suivante, il augmente à chaque exécution : à lui seul, ce n'est pas le signe d'un "
                f"problème. Dans les deux cas les exécutions restent séparées, jamais fusionnées.")
    p = f"（{pause:g} 秒）" if pause else ""
    return (f"以回答结束、随后又出现调用的运行：{n} 次。这个计数合并了 tracetrim 无法区分的两种情况：代理过早结束了运行并继续"
            f"同一任务，或者下一个任务在空闲间隔{p}内就开始了。在用户会立即打开下一段对话的宿主上，它每次运行都会增加："
            f"单凭它并不说明有问题。两种情况下，运行都保持分开，从不合并。")
