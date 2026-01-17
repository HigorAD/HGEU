
# app.py — Horários (sem cache; arquivos locais; perfis público/admin por URL)
# Python 3.8+ compatível (sem usar list[str] nem A|B)

import os
import re
import csv
import unicodedata
import importlib.util
from typing import List, Optional, Tuple, Dict, Any

import streamlit as st
import pandas as pd

# =========================
# Import robusto de convert.py (usa process_df)
# =========================
HERE = os.path.dirname(os.path.abspath(__file__))
try:
    from convert import process_df  # type: ignore
except ModuleNotFoundError:
    convert_path = os.path.join(HERE, "convert.py")
    spec = importlib.util.spec_from_file_location("convert", convert_path)
    convert = importlib.util.module_from_spec(spec)  # type: ignore
    assert spec and spec.loader
    spec.loader.exec_module(convert)  # type: ignore
    process_df = convert.process_df  # type: ignore

# =========================
# Arquivos locais (na mesma pasta do app)
# =========================
DATA_CSV = os.path.join(HERE, "TabelaGeralDisicplinas_2026_1.csv")
DATA_XLSX = os.path.join(HERE, "TabelaGeralDisicplinas_2026_1.xlsx")
DISPONIBILIDADE_SOURCE = os.path.join(HERE, "disponibilidade_professores.csv")


def find_data_source() -> str:
    """Escolhe o arquivo de horário existente: CSV ou XLSX (nessa ordem)."""
    if os.path.exists(DATA_CSV):
        return DATA_CSV
    if os.path.exists(DATA_XLSX):
        return DATA_XLSX
    return ""


# =========================
# Config Streamlit + CSS leve
# =========================
st.set_page_config(page_title="Horários — Professores e Alunos", layout="wide")
st.markdown(
    """
<style>
/* Melhor leitura: quebra de linha dentro das células das grades */
div[data-testid="stDataFrame"] div[role="gridcell"] {
    white-space: pre-wrap !important;
    line-height: 1.25;
}
</style>
""",
    unsafe_allow_html=True,
)
st.title("Horários — Professores e Alunos - 2026/1")

# =========================
# Perfis simples por URL (sem token): ?role=admin habilita abas gerenciais
# =========================
def get_query_params() -> Dict[str, str]:
    try:
        # Streamlit >= 1.30
        return dict(st.query_params)
    except Exception:
        # Compatibilidade
        return {
            k: (v[0] if isinstance(v, list) and v else v)
            for k, v in st.experimental_get_query_params().items()  # type: ignore[attr-defined]
        }

qp = get_query_params()
role = (qp.get("role") or "public").strip().lower()
is_admin = (role == "admin")

# =========================
# Constantes de exibição
# =========================
COLS_TURNOS = ["Pré", "1º", "2º"]
DIAS_ORD = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta"]
DIAS_DISP = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado"]

# =========================
# Período (Manhã/Noite) conforme regra: A,B = Manhã | P,Q,R,S = Noite
# =========================
PERIODO_MAP = {
    "A": "Manhã",
    "B": "Manhã",
    "P": "Noite",
    "Q": "Noite",
    "R": "Noite",
    "S": "Noite",
}


def infer_periodo(turma_id: str) -> str:
    """
    Inferir período a partir do código (captura A/B/P/Q/R/S entre números, p.ex. CC1P12 -> P).
    """
    if not isinstance(turma_id, str):
        return "Indefinido"
    t = turma_id.strip().upper()
    # um dígito, uma letra [ABPQRS], um dígito
    m = re.search(r"(?<=\d)([ABPQRS])(?=\d)", t)
    if not m:
        return "Indefinido"
    return PERIODO_MAP.get(m.group(1), "Indefinido")


# =========================
# Estilo por tipo (T, P, EAD)
# =========================
TYPE_STYLE = {
    "T": "background-color: #E8F1FF; color: #0B3D91; font-weight: 600;",
    "P": "background-color: #E9FBEA; color: #0F5D1A; font-weight: 600;",
    "EAD": "background-color: #FFF3E0; color: #8A4B00; font-weight: 600;",
}


def style_cell_by_tipo(val: Any) -> str:
    """
    Se a célula contém [EAD], [P] ou [T], aplica cor.
    Prioridade: EAD > P > T (em célula mista).
    """
    if not isinstance(val, str) or not val.strip():
        return ""
    v = val.upper()
    if "[EAD]" in v:
        return TYPE_STYLE["EAD"]
    if "[P]" in v:
        return TYPE_STYLE["P"]
    if "[T]" in v:
        return TYPE_STYLE["T"]
    return ""


# =========================
# Helpers de comparação/normalização de nomes
# =========================
def norm_text(s: str) -> str:
    if not isinstance(s, str):
        return ""
    s = s.strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\s+", " ", s)
    return s


def best_match_nome(nome_dispon: str, nomes_horario: List[str]) -> Optional[str]:
    """
    Casa nome completo (disponibilidade) com nome do horário (abreviado).
    Estratégia: se algum nome do horário for substring do nome completo (ou vice-versa), pega o mais longo.
    """
    nd = norm_text(nome_dispon)
    cand: List[Tuple[int, str]] = []
    for nh in nomes_horario:
        nnh = norm_text(nh)
        if nnh and (nnh in nd):
            cand.append((len(nnh), nh))
        elif nd and (nd in nnh):
            cand.append((len(nd), nh))
    if not cand:
        return None
    cand.sort(reverse=True)
    return cand[0][1]


# =========================
# Leitura do arquivo de horário e conversão (sem cache)
# =========================
def load_and_convert_horario(path: str) -> Dict[str, pd.DataFrame]:
    if not path or not os.path.exists(path):
        raise FileNotFoundError(
            "Arquivo de horário não encontrado. Coloque ao lado do app:\n"
            "- TabelaGeralDisicplinas_2026_1.csv  ou\n"
            "- TabelaGeralDisicplinas_2026_1.xlsx"
        )
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        df_raw = pd.read_csv(path, dtype=str, keep_default_na=False, comment="#", skip_blank_lines=True)
    else:
        df_raw = pd.read_excel(path, dtype=str, engine="openpyxl")
    dfs, _report = process_df(df_raw)
    return dfs


# =========================
# Leitura de disponibilidade (2 formatos)
# =========================
def load_disponibilidade(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()

    # Lê linhas cruas para detectar delimitador e blocos
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        raw_lines = [ln.rstrip("\n") for ln in f.readlines()]

    # remove comentários; preserva vazias para separar blocos
    lines: List[str] = []
    for ln in raw_lines:
        if ln.lstrip().startswith("#"):
            continue
        lines.append(ln)

    # Caso (1): header com Periodo presente
    joined_non_empty = "\n".join([ln for ln in lines if ln.strip()])
    if joined_non_empty:
        first_line = joined_non_empty.splitlines()[0]
        if re.search(r"(,|;|\t)\s*Periodo\s*(,|;|\t|$)", first_line, flags=re.I):
            sep = "\t" if "\t" in first_line else (";" if ";" in first_line else ",")
            df = pd.read_csv(path, dtype=str, keep_default_na=False, sep=sep, comment="#", skip_blank_lines=True)
            df.columns = [c.strip() for c in df.columns]
            for c in ["Periodo", "Funcional", "Nome", "NomeHorario"]:
                if c in df.columns:
                    df[c] = df[c].astype(str).str.strip()
            for d in DIAS_DISP:
                if d not in df.columns:
                    df[d] = ""
                df[d] = df[d].astype(str).str.strip().str.lower()
            # limpa linhas vazias ",,,,,,,"
            if "Funcional" in df.columns:
                df["Funcional"] = df["Funcional"].astype(str).str.strip()
            if "Nome" in df.columns:
                df["Nome"] = df["Nome"].astype(str).str.strip()
            df = df[~((df.get("Funcional", "") == "") & (df.get("Nome", "") == ""))].copy()
            return df

    # Caso (2): dois blocos com cabeçalho repetido (1º=Noite, 2º=Manhã)
    header_line = None
    for ln in lines:
        if ln.strip() and ("Funcional" in ln and "Nome" in ln):
            header_line = ln
            break
    if header_line is None:
        return pd.DataFrame()
    sep = "\t" if "\t" in header_line else (";" if ";" in header_line else ",")

    rows: List[Dict[str, str]] = []
    current_header: Optional[List[str]] = None
    block_idx = -1  # 0=noite, 1=manhã

    for ln in lines:
        if not ln.strip():
            continue
        parts = next(csv.reader([ln], delimiter=sep))
        parts = [p.strip() for p in parts]
        if parts and parts[0].lower() == "funcional":
            current_header = parts
            block_idx += 1
            continue
        if current_header is None:
            continue
        if len(parts) < len(current_header):
            parts = parts + [""] * (len(current_header) - len(parts))
        rec = dict(zip(current_header, parts))
        periodo = "Noite" if block_idx == 0 else ("Manhã" if block_idx == 1 else "Indefinido")
        rec["Periodo"] = periodo
        for d in DIAS_DISP:
            rec.setdefault(d, "")
        rows.append(rec)

    df = pd.DataFrame(rows)
    df.columns = [c.strip() for c in df.columns]
    for d in DIAS_DISP:
        df[d] = df[d].astype(str).str.strip().str.lower()
    if "NomeHorario" not in df.columns:
        df["NomeHorario"] = ""
    # limpa linhas vazias
    if "Funcional" in df.columns:
        df["Funcional"] = df["Funcional"].astype(str).str.strip()
    if "Nome" in df.columns:
        df["Nome"] = df["Nome"].astype(str).str.strip()
    df = df[~((df.get("Funcional", "") == "") & (df.get("Nome", "") == ""))].copy()
    return df


# =========================
# Helper: exibir grade com estilo por tipo
# =========================
def show_grid(df_grid: pd.DataFrame):
    grid = df_grid.copy()
    if grid.index.name is not None:
        grid = grid.reset_index().rename(columns={"dia_semana": "Dia"})

    try:
        styler = grid.style.applymap(style_cell_by_tipo, subset=[c for c in COLS_TURNOS if c in grid.columns])
        col_cfg = {
            "Dia": st.column_config.TextColumn("Dia", width="small"),
            "Pré": st.column_config.TextColumn("Pré", width="medium"),
            "1º": st.column_config.TextColumn("1º", width="medium"),
            "2º": st.column_config.TextColumn("2º", width="medium"),
        }
        for k in list(col_cfg.keys()):
            if k not in grid.columns:
                col_cfg.pop(k, None)
        st.dataframe(styler, use_container_width=True, column_config=col_cfg)
    except Exception:
        st.dataframe(grid, use_container_width=True)


# =========================
# Carregar dados do horário e montar DF enriquecido
# =========================
try:
    data_path = find_data_source()
    dfs = load_and_convert_horario(data_path)
except Exception as e:
    st.error(str(e))
    st.stop()

cursos = dfs["cursos"]
turmas = dfs["turmas"]
disciplinas = dfs["disciplinas"]
professores = dfs["professores"]
agenda = dfs["agenda"]

DF = (
    agenda.merge(disciplinas, on="disc_id", how="left")
    .merge(professores, on="prof_id", how="left")
    .merge(turmas, on="turma_id", how="left")
    .merge(cursos, on="curso_id", how="left")
)
DF["periodo"] = DF["turma_id"].apply(infer_periodo)

# =========================
# TABS (público/admin) — inclui "Horários (Todas as Turmas)"
# =========================
tabs_public = ["Horário por Turma", "Agenda do Professor", "Horários (Todas as Turmas)"]
tabs_labels = tabs_public + (["Disponibilidade (Professores)", "Conflitos"] if is_admin else [])
tabs = st.tabs(tabs_labels)

if is_admin:
    st.info("🔒 Modo Coordenação (admin). Adicionais: Disponibilidade e Conflitos.")

# Mapeia tabs para variáveis (conforme role)
if is_admin:
    tab_turma, tab_prof, tab_all, tab_disp, tab_conf = tabs  # type: ignore[misc]
else:
    tab_turma, tab_prof, tab_all = tabs  # type: ignore[misc]

# =========================
# Aba 1 — Horário por Turma (com agrupamento de mesma disciplina)
# =========================
with tab_turma:
    turma_sel = st.selectbox("Selecione a turma", sorted(DF["turma_id"].dropna().unique().tolist()))
    df_turma = DF[DF["turma_id"] == turma_sel].copy()

    # Agrupa por dia/turno + disciplina + tipo e une professores
    grouped = (
        df_turma.groupby(["dia_semana", "turno", "nome_disciplina", "tipo"], dropna=False)
        .agg(profs=("nome_professor", lambda s: sorted(set([p for p in s if isinstance(p, str) and p.strip()]))))
        .reset_index()
    )

    def fmt_cell(row: pd.Series) -> str:
        disc = row.get("nome_disciplina") or ""
        tipo = row.get("tipo") or ""
        profs = row.get("profs") or []
        profs_txt = " / ".join(profs) if profs else ""
        return f"{disc} ({profs_txt}) [{tipo}]".strip()

    grouped["cell"] = grouped.apply(fmt_cell, axis=1)

    pivot = (
        grouped.pivot_table(
            index="dia_semana",
            columns="turno",
            values="cell",
            aggfunc=lambda x: "\n".join([v for v in x if isinstance(v, str) and v.strip()]),
        )
        .reindex(DIAS_ORD)
        .reindex(columns=COLS_TURNOS)
    )

    st.subheader(f"Horário — Turma {turma_sel}")
    show_grid(pivot)
    st.caption('Cada linha: "Disciplina (Professor(es)) [Tipo]". Tipos: T=Teórica, P=Prática, EAD=Assíncrona.')

# =========================
# Aba 2 — Agenda do Professor (separada por Manhã/Noite/Indefinido)
# =========================
with tab_prof:
    prof_sel = st.selectbox("Selecione o professor", sorted(DF["nome_professor"].dropna().unique().tolist()))
    df_prof = DF[DF["nome_professor"] == prof_sel].copy()

    def montar_grade_prof(df_base: pd.DataFrame) -> pd.DataFrame:
        grouped = (
            df_base.groupby(["dia_semana", "turno", "nome_disciplina", "tipo"], dropna=False)
            .agg(turmas=("turma_id", lambda s: sorted(set([x for x in s if isinstance(x, str) and x.strip()]))))
            .reset_index()
        )

        def fmt_cell(row: pd.Series) -> str:
            disc = row["nome_disciplina"] or ""
            tipo = row["tipo"] or ""
            turmas_txt = " / ".join(row["turmas"]) if row["turmas"] else ""
            return f"{disc} [{tipo}] ({turmas_txt})".strip()

        grouped["cell"] = grouped.apply(fmt_cell, axis=1)
        pivot_prof = (
            grouped.pivot_table(
                index="dia_semana",
                columns="turno",
                values="cell",
                aggfunc=lambda x: "\n".join([v for v in x if isinstance(v, str) and v.strip()]),
            )
            .reindex(DIAS_ORD)
            .reindex(columns=COLS_TURNOS)
        )
        return pivot_prof

    st.subheader(f"Agenda — {prof_sel}")
    df_manha = df_prof[df_prof["periodo"] == "Manhã"]
    df_noite = df_prof[df_prof["periodo"] == "Noite"]
    df_indef = df_prof[df_prof["periodo"] == "Indefinido"]

    if not df_manha.empty:
        st.markdown("### ☀️ Manhã")
        show_grid(montar_grade_prof(df_manha))
    if not df_noite.empty:
        st.markdown("### 🌙 Noite")
        show_grid(montar_grade_prof(df_noite))
    if not df_indef.empty:
        st.markdown("### ❓ Indefinido")
        show_grid(montar_grade_prof(df_indef))

# =========================
# Aba 3 (pública) — Horários (Todas as Turmas)
# =========================
with tab_all:
    st.subheader("Horários — Todas as Turmas")

    # Filtros auxiliares
    curso_opts = ["(Todos)"] + sorted(DF["curso_nome"].dropna().unique().tolist())
    curso_sel = st.selectbox("Curso", curso_opts, index=0)

    periodo_opts = ["(Todos)", "Manhã", "Noite", "Indefinido"]
    periodo_sel = st.selectbox("Período", periodo_opts, index=0)

    ocultar_vazias = st.checkbox("Ocultar turmas sem aulas no filtro atual", value=True)

    # Base de turmas conforme curso
    if curso_sel == "(Todos)":
        base = DF.copy()
    else:
        base = DF[DF["curso_nome"] == curso_sel].copy()

    turmas_list = sorted(base["turma_id"].dropna().unique().tolist())

    # Helper: montar pivot de uma turma (mesma lógica da aba por turma)
    def pivot_turma(df_turma: pd.DataFrame) -> pd.DataFrame:
        grouped = (
            df_turma.groupby(["dia_semana", "turno", "nome_disciplina", "tipo"], dropna=False)
            .agg(profs=("nome_professor", lambda s: sorted(set([p for p in s if isinstance(p, str) and p.strip()]))))
            .reset_index()
        )
        def fmt_cell(row: pd.Series) -> str:
            disc = row.get("nome_disciplina") or ""
            tipo = row.get("tipo") or ""
            profs = row.get("profs") or []
            profs_txt = " / ".join(profs) if profs else ""
            return f"{disc} ({profs_txt}) [{tipo}]".strip()
        grouped["cell"] = grouped.apply(fmt_cell, axis=1)
        pivot = (
            grouped.pivot_table(
                index="dia_semana",
                columns="turno",
                values="cell",
                aggfunc=lambda x: "\n".join([v for v in x if isinstance(v, str) and v.strip()]),
            )
            .reindex(DIAS_ORD)
            .reindex(columns=COLS_TURNOS)
        )
        return pivot

    # Renderização por turma (com filtros)
    if not turmas_list:
        st.info("Nenhuma turma encontrada para o filtro selecionado.")
    else:
        for turma_id in turmas_list:
            df_t = DF[DF["turma_id"] == turma_id].copy()
            if periodo_sel != "(Todos)":
                df_t = df_t[df_t["periodo"] == periodo_sel]
            if df_t.empty and ocultar_vazias:
                continue

            pivot = pivot_turma(df_t)
            # Se todas as células estiverem vazias e 'ocultar' estiver marcado, pula
            if ocultar_vazias:
                is_all_empty = True
                if pivot is not None and not pivot.empty:
                    is_all_empty = pivot.fillna("").applymap(lambda x: str(x).strip() == "").all().all()
                if is_all_empty:
                    continue

            st.markdown(f"### Turma {turma_id}")
            show_grid(pivot)
            st.divider()

# =========================
# Aba 4 — Disponibilidade (Professores) — somente admin
# =========================
if is_admin:
    with st.tabs(["Disponibilidade (Professores)"])[0]:  # preserva ordem visual com abas gerenciais
        st.subheader("Disponibilidade x Atribuição (por Professor)")

        disp_df = load_disponibilidade(DISPONIBILIDADE_SOURCE)
        if disp_df.empty:
            st.warning(
                "Arquivo `disponibilidade_professores.csv` não encontrado ou vazio na pasta do app.\n"
                "Coloque-o ao lado do `app.py` para habilitar esta aba."
            )
        else:
            # Dias disponíveis por linha (x)
            def dias_disp(row: pd.Series) -> List[str]:
                out: List[str] = []
                for d in DIAS_DISP:
                    if str(row.get(d, "")).strip().lower() == "x":
                        out.append(d)
                return out

            disp_df["dias_disponiveis"] = disp_df.apply(dias_disp, axis=1)

            # Resolver nome do horário (NomeHorario prioritário; senão casar automaticamente)
            nomes_horario = sorted(DF["nome_professor"].dropna().unique().tolist())

            def resolve_nome_horario(row: pd.Series) -> str:
                nh = str(row.get("NomeHorario", "")).strip()
                if nh:
                    return nh
                nome = str(row.get("Nome", "")).strip()
                return best_match_nome(nome, nomes_horario) or ""

            disp_df["NomeHorario_resolvido"] = disp_df.apply(resolve_nome_horario, axis=1)

            # Atribuições (por período)
            periodo_sel = st.selectbox("Período", ["Manhã", "Noite"])
            df_p = DF[DF["periodo"] == periodo_sel].copy()
            disp_p = disp_df[disp_df["Periodo"].astype(str).str.lower() == periodo_sel.lower()].copy()

            atribu = (
                df_p.groupby("nome_professor")["dia_semana"]
                .apply(lambda s: sorted(set([x for x in s if isinstance(x, str) and x.strip()])))
                .reset_index(name="dias_atribuidos")
            )

            base = disp_p.merge(
                atribu, left_on="NomeHorario_resolvido", right_on="nome_professor", how="left"
            )
            base["dias_atribuidos"] = base["dias_atribuidos"].apply(lambda x: x if isinstance(x, list) else [])

            base["disp_set"] = base["dias_disponiveis"].apply(lambda x: set(x) if isinstance(x, list) else set())
            base["atr_set"] = base["dias_atribuidos"].apply(lambda x: set(x) if isinstance(x, list) else set())

            base["Disponível"] = base["disp_set"].apply(lambda s: ", ".join(sorted(s)))
            base["Atribuído"] = base["atr_set"].apply(lambda s: ", ".join(sorted(s)))
            base["Disponível e Livre"] = (base["disp_set"] - base["atr_set"]).apply(lambda s: ", ".join(sorted(s)))
            base["Atribuído fora da disponibilidade"] = (base["atr_set"] - base["disp_set"]).apply(
                lambda s: ", ".join(sorted(s))
            )

            # Alertas de nomes não casados
            nao_casados = base[(base["NomeHorario_resolvido"] == "") & (base["Atribuído"] == "")]
            if not nao_casados.empty:
                st.info(
                    "Alguns professores não foram casados automaticamente com o nome do horário.\n"
                    "👉 Para ficar perfeito, preencha a coluna **NomeHorario** no arquivo de disponibilidade\n"
                    "com o mesmo nome que aparece no horário."
                )
                st.dataframe(nao_casados[["Funcional", "Nome", "Periodo"]], use_container_width=True)

            resumo = base[
                ["Funcional", "Nome", "NomeHorario_resolvido", "Disponível", "Atribuído",
                 "Disponível e Livre", "Atribuído fora da disponibilidade"]
            ].rename(columns={"NomeHorario_resolvido": "NomeHorario"})
            st.markdown("#### Resumo (por professor)")
            st.dataframe(resumo, use_container_width=True)

            st.markdown("#### Visão por dia (matriz)")
            def status_por_dia(row: pd.Series, dia: str) -> str:
                d_disp = dia in row["disp_set"]
                d_atr = dia in row["atr_set"]
                if d_atr and d_disp:
                    return "✅ Atribuído (OK)"
                if d_disp and not d_atr:
                    return "🟡 Livre (Disponível)"
                if d_atr and not d_disp:
                    return "🔴 Atribuído (fora disp.)"
                return "—"

            matriz = pd.DataFrame(
                {
                    "Professor": resumo["NomeHorario"].where(resumo["NomeHorario"] != "", resumo["Nome"]),
                    **{d: base.apply(lambda r: status_por_dia(r, d), axis=1) for d in DIAS_DISP},
                }
            )
            st.dataframe(matriz, use_container_width=True)

# =========================
# Aba 5 — Conflitos — somente admin
# =========================
if is_admin:
    with st.tabs(["Conflitos"])[0]:
        st.subheader("Conflitos — Disponibilidade e Choques de Horário")

        # Carrega disponibilidade
        disp_df = load_disponibilidade(DISPONIBILIDADE_SOURCE)
        if disp_df.empty:
            st.warning(
                "Arquivo `disponibilidade_professores.csv` não encontrado ou vazio na pasta do app.\n"
                "A análise de 'fora da disponibilidade' ficará indisponível."
            )

        # Resolver NomeHorario_resolvido
        nomes_horario = sorted(DF["nome_professor"].dropna().unique().tolist())
        if not disp_df.empty:
            disp_df["dias_disponiveis"] = disp_df.apply(
                lambda r: [d for d in DIAS_DISP if str(r.get(d, "")).strip().lower() == "x"], axis=1
            )

            def resolve_nome_horario(row: pd.Series) -> str:
                nh = str(row.get("NomeHorario", "")).strip()
                if nh:
                    return nh
                nome = str(row.get("Nome", "")).strip()
                return best_match_nome(nome, nomes_horario) or ""

            disp_df["NomeHorario_resolvido"] = disp_df.apply(resolve_nome_horario, axis=1)

        # Dados base para conflitos
        df_aulas = DF.copy()
        # Exclui professores vazios — evita ruído (ex.: EAD com '*')
        df_aulas = df_aulas[df_aulas["nome_professor"].notna() & (df_aulas["nome_professor"].astype(str).str.strip() != "")]

        # --- Mapa de disponibilidade por (professor, período) -> set(dias)
        disp_map: Dict[Tuple[str, str], set] = {}
        if not disp_df.empty:
            for _, r in disp_df.iterrows():
                prof_nome = str(r.get("NomeHorario_resolvido", "")).strip()
                periodo = str(r.get("Periodo", "")).strip()
                if not prof_nome or not periodo:
                    continue
                disp_map.setdefault((prof_nome, periodo), set()).update(set(r.get("dias_disponiveis", []) or []))

        # --- Conflito 1: Fora da disponibilidade (dia)
        def fora_disp(row: pd.Series) -> bool:
            key = (row.get("nome_professor"), row.get("periodo"))
            dia = row.get("dia_semana")
            if not dia or key not in disp_map:
                return False
            return dia not in disp_map[key]

        df_fora = df_aulas[df_aulas.apply(fora_disp, axis=1)].copy()

        # --- Conflito 2: Choques por professor (duas+ disciplinas no mesmo dia/turno)
        g = (
            df_aulas.groupby(["nome_professor", "periodo", "dia_semana", "turno"], dropna=False)
            .agg(
                n_disc=("nome_disciplina", lambda s: len(set([x for x in s if isinstance(x, str) and x.strip()]))),
                disciplinas=("nome_disciplina", lambda s: sorted(set([x for x in s if isinstance(x, str) and x.strip()]))),
                turmas=("turma_id", lambda s: sorted(set([x for x in s if isinstance(x, str) and x.strip()]))),
                tipos=("tipo", lambda s: sorted(set([str(x).strip() for x in s if str(x).strip()]))),
            )
            .reset_index()
        )
        df_choques_prof = g[g["n_disc"] > 1].copy()

        # --- Conflito 3: Choques por turma (ignora turno "Pré")
        df_aulas_turma = df_aulas[df_aulas["turno"].astype(str).str.strip() != "Pré"].copy()
        gt = (
            df_aulas_turma.groupby(["turma_id", "periodo", "dia_semana", "turno"], dropna=False)
            .agg(
                n_disc=("nome_disciplina", lambda s: len(set([x for x in s if isinstance(x, str) and x.strip()]))),
                disciplinas=("nome_disciplina", lambda s: sorted(set([x for x in s if isinstance(x, str) and x.strip()]))),
                professores=("nome_professor", lambda s: sorted(set([x for x in s if isinstance(x, str) and x.strip()]))),
                tipos=("tipo", lambda s: sorted(set([str(x).strip() for x in s if str(x).strip()]))),
            )
            .reset_index()
        )
        df_choques_turma = gt[gt["n_disc"] > 1].copy()

        # --- Filtros
        st.markdown("### Filtros")
        professores_opts = ["(Todos)"] + sorted(df_aulas["nome_professor"].dropna().unique().tolist())
        prof_sel = st.selectbox("Professor", professores_opts, index=0)

        turmas_opts = ["(Todas)"] + sorted(df_aulas["turma_id"].dropna().unique().tolist())
        turma_sel = st.selectbox("Turma", turmas_opts, index=0)

        periodo_opts = ["(Todos)", "Manhã", "Noite", "Indefinido"]
        periodo_sel = st.selectbox("Período", periodo_opts, index=0)

        def filtra(df: pd.DataFrame) -> pd.DataFrame:
            out = df.copy()
            if prof_sel != "(Todos)" and "nome_professor" in out.columns:
                out = out[out["nome_professor"] == prof_sel]
            if turma_sel != "(Todas)" and "turma_id" in out.columns:
                out = out[out["turma_id"] == turma_sel]
            if periodo_sel != "(Todos)" and "periodo" in out.columns:
                out = out[out["periodo"] == periodo_sel]
            return out

        df_fora_f = filtra(df_fora)
        df_choques_prof_f = filtra(df_choques_prof)
        df_choques_turma_f = filtra(df_choques_turma)

        # --- Métricas
        c1, c2, c3 = st.columns(3)
        c1.metric("Fora da disponibilidade (dia)", int(len(df_fora_f)))
        c2.metric("Choques por professor", int(len(df_choques_prof_f)))
        c3.metric("Choques por turma (ignora 'Pré')", int(len(df_choques_turma_f)))

        # --- Tabelas
        st.markdown("#### 1) Aulas atribuídas fora da disponibilidade (por dia)")
        if df_fora_f.empty:
            st.success("Nenhuma atribuição fora da disponibilidade com os filtros atuais.")
        else:
            view_fora = df_fora_f[
                ["nome_professor", "periodo", "dia_semana", "turno", "turma_id", "nome_disciplina", "tipo"]
            ].sort_values(["nome_professor", "periodo", "dia_semana", "turno"])
            st.dataframe(view_fora, use_container_width=True)

        st.markdown("#### 2) Choques por professor (duas ou mais disciplinas no mesmo dia/turno)")
        if df_choques_prof_f.empty:
            st.success("Nenhum choque por professor com os filtros atuais.")
        else:
            v2 = df_choques_prof_f.copy()
            v2["disciplinas"] = v2["disciplinas"].apply(lambda x: " / ".join(x) if isinstance(x, list) else str(x))
            v2["turmas"] = v2["turmas"].apply(lambda x: " / ".join(x) if isinstance(x, list) else str(x))
            v2["tipos"] = v2["tipos"].apply(lambda x: " / ".join(x) if isinstance(x, list) else str(x))
            v2 = v2[
                ["nome_professor", "periodo", "dia_semana", "turno", "n_disc", "disciplinas", "turmas", "tipos"]
            ].sort_values(["nome_professor", "periodo", "dia_semana", "turno"])
            st.dataframe(v2, use_container_width=True)

        st.markdown("#### 3) Choques por turma (duas ou mais disciplinas no mesmo dia/turno) — ignora 'Pré'")
        if df_choques_turma_f.empty:
            st.success("Nenhum choque por turma com os filtros atuais.")
        else:
            v3 = df_choques_turma_f.copy()
            v3["disciplinas"] = v3["disciplinas"].apply(lambda x: " / ".join(x) if isinstance(x, list) else str(x))
            v3["professores"] = v3["professores"].apply(lambda x: " / ".join(x) if isinstance(x, list) else str(x))
            v3["tipos"] = v3["tipos"].apply(lambda x: " / ".join(x) if isinstance(x, list) else str(x))
            v3 = v3[
                ["turma_id", "periodo", "dia_semana", "turno", "n_disc", "disciplinas", "professores", "tipos"]
            ].sort_values(["turma_id", "periodo", "dia_semana", "turno"])
            st.dataframe(v3, use_container_width=True)
