from __future__ import annotations

import os
import sys
from pathlib import Path


SDK_PATH = Path(__file__).resolve().parents[2] / "sdk" / "python"
if str(SDK_PATH) not in sys.path:
    sys.path.insert(0, str(SDK_PATH))

from ragflow_sdk.ragflow import RAGFlow  # type: ignore  # noqa: E402


def _parse_csv_ids(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


def _retrieval_tool(kb_ids: list[str], description: str) -> dict:
    return {
        "component_name": "Retrieval",
        "name": "Retrieval",
        "params": {
            "cross_languages": [],
            "description": description,
            "empty_response": "知识库未检索到直接证据，请提示用户补充关键词或上传相关文档。",
            "kb_ids": kb_ids,
            "keywords_similarity_weight": 0.7,
            "outputs": {
                "formalized_content": {
                    "type": "string",
                    "value": "",
                }
            },
            "rerank_id": "",
            "similarity_threshold": 0.2,
            "top_k": 1024,
            "top_n": 8,
            "use_kg": False,
        },
    }


def build_qms_3core_dsl(llm_id: str, kb_ids: list[str]) -> dict:
    retrieval_desc = "SSME DX & SSME US QMS文档库，含QM/TP/WI、模板及法规要求。"

    def _agent_common(max_rounds: int, history_window: int, temperature: float) -> dict:
        return {
            "llm_id": llm_id,
            "max_rounds": max_rounds,
            "message_history_window_size": history_window,
            "max_retries": 2,
            "delay_after_error": 1.5,
            "cite": True,
            "temperatureEnabled": True,
            "temperature": temperature,
            "maxTokensEnabled": True,
            "max_tokens": 1800,
            "topPEnabled": True,
            "top_p": 0.7,
            "outputs": {"content": {"type": "string", "value": ""}},
        }

    def _nid(cpn_id: str) -> str:
        return cpn_id.replace(":", "_")

    begin_params = {
        "mode": "conversational",
        "prologue": "你好，我是QMS企业智能助手。已启用分层编排：理解-记忆-检索-推理-生成-学习。",
        "inputs": {
            "sys.query": {"type": "line", "name": "问题"},
            "sys.files": {"type": "file", "name": "附件", "optional": True},
        },
    }

    st_input_parse_params = {
        "method": "merge",
        "script": "【SSME-QMS 输入理解包】\\n"
                  "原始问题：{sys.query}\\n"
                  "用户ID：{sys.user_id}\\n"
                  "日期：{sys.date}\\n"
                  "附件状态：{sys.files}\\n"
                  "\\n"
                  "【改写目标】\\n"
                  "1) 保留用户业务意图，不改写事实含义；\\n"
                  "2) 补齐场景标签：process_qa / learning / precheck；\\n"
                  "3) 生成中英检索关键词（术语优先：NCM/CAPA/ISO13485/ISO14971等）；\\n"
                  "4) 标注回答约束：evidence_first, no_fabrication, actionable_steps。\\n"
                  "\\n"
                  "【输出格式要求】\\n"
                  "- rewrite_query: <改写后的标准问题>\\n"
                  "- scenario_hint: <process_qa|learning|precheck>\\n"
                  "- retrieval_terms_zh: <中文关键词，逗号分隔>\\n"
                  "- retrieval_terms_en: <英文关键词，逗号分隔>\\n"
                  "- constraints: <证据优先|禁止编造|可执行步骤>\\n"
                  "- note: <若信息不足，说明缺口>\n",
        "delimiters": ["\n"],
        "outputs": {"result": {"value": "", "type": "string"}},
    }

    intent_classify_params = {
        **_agent_common(max_rounds=4, history_window=6, temperature=0.1),
        "tools": [],
        "prompts": [{"role": "user", "content": "{st:input_parse@result}"}],
        "sys_prompt": "你是意图识别器。输出结构化意图JSON（intent/category/urgency/keywords），不要解释。",
    }
    entity_extract_params = {
        **_agent_common(max_rounds=4, history_window=6, temperature=0.1),
        "tools": [],
        "prompts": [{"role": "user", "content": "意图识别结果：{agent:intent_classify@content}\\n问题：{sys.query}"}],
        "sys_prompt": "你是实体提取器。提取部门、流程、文档、术语、时间、产品，输出JSON数组。",
    }
    memory_hot_params = {
        **_agent_common(max_rounds=3, history_window=12, temperature=0.1),
        "tools": [],
        "prompts": [{"role": "user", "content": "历史：{sys.history}\\n问题：{sys.query}\\n实体：{agent:entity_extract@content}"}],
        "sys_prompt": "你是热记忆检索器。输出用户偏好、输出风格、避免点、近期目标（最多6条）。",
    }
    memory_warm_params = {
        **_agent_common(max_rounds=3, history_window=14, temperature=0.1),
        "tools": [],
        "prompts": [{"role": "user", "content": "意图：{agent:intent_classify@content}\\n实体：{agent:entity_extract@content}"}],
        "sys_prompt": "你是温记忆检索器。输出场景规则、SOP提醒、适用约束（最多6条）。",
    }

    switch_intent_params = {
        "conditions": [
            {
                "logical_operator": "or",
                "items": [
                    {"cpn_id": "sys.query", "operator": "contains", "value": "培训"},
                    {"cpn_id": "sys.query", "operator": "contains", "value": "学习"},
                    {"cpn_id": "sys.query", "operator": "contains", "value": "讲解"},
                ],
                "to": ["st:learning"],
            },
            {
                "logical_operator": "or",
                "items": [
                    {"cpn_id": "sys.query", "operator": "contains", "value": "预审"},
                    {"cpn_id": "sys.query", "operator": "contains", "value": "附件"},
                    {"cpn_id": "sys.query", "operator": "contains", "value": "文档审查"},
                    {"cpn_id": "sys.query", "operator": "contains", "value": "文件审查"},
                ],
                "to": ["switch:precheck_files"],
            },
        ],
        "end_cpn_ids": ["st:process"],
    }

    switch_precheck_files_params = {
        "conditions": [
            {
                "logical_operator": "and",
                "items": [{"cpn_id": "sys.files", "operator": "not empty", "value": ""}],
                "to": ["list:precheck"],
            }
        ],
        "end_cpn_ids": ["msg:precheckneedfile"],
    }
    list_precheck_params = {"query": "sys.files", "operations": "topN", "n": 3}

    branch_desc = {
        "process": {
            "title": "流程问答",
            "st_script": "【流程问答】\\n问题：{sys.query}\\n热记忆：{agent:memory_hot@content}\\n温记忆：{agent:memory_warm@content}\\n实体：{agent:entity_extract@content}",
            "skill_prompt": "你是QMS流程专家。基于知识库给出步骤、控制点、职责、模板、常见错误。",
            "y": 100,
        },
        "learning": {
            "title": "体系学习",
            "st_script": "【体系学习】\\n问题：{sys.query}\\n热记忆：{agent:memory_hot@content}\\n温记忆：{agent:memory_warm@content}\\n实体：{agent:entity_extract@content}",
            "skill_prompt": "你是QMS培训专家。输出：学习目标、输入-活动-输出、岗位落地建议、3道自测题。",
            "y": 380,
        },
        "precheck": {
            "title": "文件预审",
            "st_script": "【文件预审】\\n问题：{sys.query}\\n附件：{list:precheck@result}\\n热记忆：{agent:memory_hot@content}\\n温记忆：{agent:memory_warm@content}",
            "skill_prompt": "你是QMS预审专家。输出：结论、问题分级、证据条款、整改建议。",
            "y": 680,
        },
    }

    components: dict = {
        "begin": {"obj": {"component_name": "Begin", "params": begin_params}, "downstream": ["st:input_parse"], "upstream": []},
        "st:input_parse": {"obj": {"component_name": "StringTransform", "params": st_input_parse_params}, "downstream": ["agent:intent_classify"], "upstream": ["begin"]},
        "agent:intent_classify": {"obj": {"component_name": "Agent", "params": intent_classify_params}, "downstream": ["agent:entity_extract"], "upstream": ["st:input_parse"]},
        "agent:entity_extract": {"obj": {"component_name": "Agent", "params": entity_extract_params}, "downstream": ["agent:memory_hot"], "upstream": ["agent:intent_classify"]},
        "agent:memory_hot": {"obj": {"component_name": "Agent", "params": memory_hot_params}, "downstream": ["agent:memory_warm"], "upstream": ["agent:entity_extract"]},
        "agent:memory_warm": {"obj": {"component_name": "Agent", "params": memory_warm_params}, "downstream": ["switch:intent"], "upstream": ["agent:memory_hot"]},
        "switch:intent": {"obj": {"component_name": "Switch", "params": switch_intent_params}, "downstream": ["st:process", "st:learning", "switch:precheck_files"], "upstream": ["agent:memory_warm"]},
        "switch:precheck_files": {"obj": {"component_name": "Switch", "params": switch_precheck_files_params}, "downstream": ["list:precheck", "msg:precheckneedfile"], "upstream": ["switch:intent"]},
        "list:precheck": {"obj": {"component_name": "ListOperations", "params": list_precheck_params}, "downstream": ["st:precheck"], "upstream": ["switch:precheck_files"]},
        "msg:precheckneedfile": {
            "obj": {
                "component_name": "Message",
                "params": {"content": ["当前未检测到附件。请上传待预审文件（如NCM、CAPA、风险评估、管理评审记录）后再进行符合性审查。"]},
            },
            "downstream": [],
            "upstream": ["switch:precheck_files"],
        },
    }

    for b, meta in branch_desc.items():
        st_params = {
            "method": "merge",
            "script": meta["st_script"],
            "delimiters": ["\n"],
            "outputs": {"result": {"value": "", "type": "string"}},
        }
        memory_params = {
            **_agent_common(max_rounds=3, history_window=16, temperature=0.1),
            "tools": [],
            "prompts": [{"role": "user", "content": "规范问题：{st:" + b + "@result}\\n历史：{sys.history}"}],
            "sys_prompt": "你是记忆整理器。提炼本轮可复用规则、偏好和未决项（最多6条）。",
        }
        assign_params = {
            "variables": [{"variable": "sys.query", "operator": "overwrite", "parameter": "memory:" + b + "@content"}],
        }
        knowledge_params = {
            **_agent_common(max_rounds=6, history_window=14, temperature=0.1),
            "tools": [_retrieval_tool(kb_ids, retrieval_desc)],
            "prompts": [{"role": "user", "content": "检索问题：{st:" + b + "@result}"}],
            "sys_prompt": "你是知识处理器。完成检索、重排、摘要，并输出关键证据。",
        }
        strategy_switch_params = {
            "conditions": [
                {
                    "logical_operator": "or",
                    "items": [
                        {"cpn_id": "sys.query", "operator": "contains", "value": "计算"},
                        {"cpn_id": "sys.query", "operator": "contains", "value": "统计"},
                        {"cpn_id": "sys.query", "operator": "contains", "value": "对比"},
                        {"cpn_id": "sys.query", "operator": "contains", "value": "清单"},
                    ],
                    "to": ["agent:plan_" + b],
                }
            ],
            "end_cpn_ids": ["agent:direct_" + b],
        }
        direct_params = {
            **_agent_common(max_rounds=6, history_window=12, temperature=0.15),
            "tools": [],
            "prompts": [{"role": "user", "content": "知识摘要：{agent:knowledge_" + b + "@content}\\n用户问题：{st:" + b + "@result}"}],
            "sys_prompt": meta["skill_prompt"],
        }
        plan_params = {
            **_agent_common(max_rounds=4, history_window=10, temperature=0.1),
            "tools": [],
            "prompts": [{"role": "user", "content": "知识摘要：{agent:knowledge_" + b + "@content}\\n用户问题：{st:" + b + "@result}"}],
            "sys_prompt": "你是工具规划器。输出多步执行计划，标明依赖关系与顺序。",
        }
        exec_params = {
            **_agent_common(max_rounds=5, history_window=10, temperature=0.1),
            "tools": [],
            "prompts": [{"role": "user", "content": "执行计划：{agent:plan_" + b + "@content}\\n知识摘要：{agent:knowledge_" + b + "@content}"}],
            "sys_prompt": "你是执行调度器。按计划产出可执行结果，遇到不确定项需标注。",
        }
        integrate_params = {
            **_agent_common(max_rounds=5, history_window=12, temperature=0.1),
            "tools": [],
            "prompts": [{"role": "user", "content": "直接结果：{agent:direct_" + b + "@content}\\n计划执行结果：{agent:exec_" + b + "@content}"}],
            "sys_prompt": "你是结果整合器。合并多源结果，去重并保持一致性。",
        }
        format_params = {
            **_agent_common(max_rounds=3, history_window=8, temperature=0.1),
            "tools": [],
            "prompts": [{"role": "user", "content": "整合结果：{agent:integrate_" + b + "@content}"}],
            "sys_prompt": "你是格式优化器。按Markdown输出：结论、步骤、控制点、引用证据。",
        }
        eval_params = {
            **_agent_common(max_rounds=3, history_window=8, temperature=0.1),
            "tools": [],
            "prompts": [{"role": "user", "content": "输出草稿：{agent:format_" + b + "@content}\\n原问题：{st:" + b + "@result}"}],
            "sys_prompt": "你是质量评估器。输出最终答案，并在首行严格输出 NEED_IMPROVE: YES 或 NEED_IMPROVE: NO。",
        }
        need_improve_params = {
            "conditions": [
                {
                    "logical_operator": "or",
                    "items": [{"cpn_id": "eval:" + b + "@content", "operator": "contains", "value": "NEED_IMPROVE: YES"}],
                    "to": ["agent:reflect_" + b],
                }
            ],
            "end_cpn_ids": ["assign:final_eval_" + b],
        }
        reflect_params = {
            **_agent_common(max_rounds=3, history_window=8, temperature=0.1),
            "tools": [],
            "prompts": [{"role": "user", "content": "待改进答案：{eval:" + b + "@content}"}],
            "sys_prompt": "你是反思分析器。给出根因诊断：理解/检索/推理/表达。",
        }
        rule_params = {
            **_agent_common(max_rounds=3, history_window=8, temperature=0.1),
            "tools": [],
            "prompts": [{"role": "user", "content": "反思结果：{agent:reflect_" + b + "@content}"}],
            "sys_prompt": "你是规则提炼器。输出IF-THEN-BECAUSE规则（最多3条）。",
        }
        memory_update_params = {
            **_agent_common(max_rounds=3, history_window=8, temperature=0.1),
            "tools": [],
            "prompts": [{"role": "user", "content": "规则：{agent:rule_" + b + "@content}\\n原答案：{eval:" + b + "@content}"}],
            "sys_prompt": "你是记忆更新器。在不改变事实证据前提下输出改进后的最终答案。",
        }

        components["st:" + b] = {"obj": {"component_name": "StringTransform", "params": st_params}, "downstream": ["memory:" + b], "upstream": ["list:precheck" if b == "precheck" else "switch:intent"]}
        components["memory:" + b] = {"obj": {"component_name": "Agent", "params": memory_params}, "downstream": ["assign:" + b], "upstream": ["st:" + b]}
        components["assign:" + b] = {"obj": {"component_name": "VariableAssigner", "params": assign_params}, "downstream": ["agent:knowledge_" + b], "upstream": ["memory:" + b]}
        components["agent:knowledge_" + b] = {"obj": {"component_name": "Agent", "params": knowledge_params}, "downstream": ["switch:strategy_" + b], "upstream": ["assign:" + b]}
        components["switch:strategy_" + b] = {"obj": {"component_name": "Switch", "params": strategy_switch_params}, "downstream": ["agent:direct_" + b, "agent:plan_" + b], "upstream": ["agent:knowledge_" + b]}
        components["agent:direct_" + b] = {"obj": {"component_name": "Agent", "params": direct_params}, "downstream": ["agent:integrate_" + b], "upstream": ["switch:strategy_" + b]}
        components["agent:plan_" + b] = {"obj": {"component_name": "Agent", "params": plan_params}, "downstream": ["agent:exec_" + b], "upstream": ["switch:strategy_" + b]}
        components["agent:exec_" + b] = {"obj": {"component_name": "Agent", "params": exec_params}, "downstream": ["agent:integrate_" + b], "upstream": ["agent:plan_" + b]}
        components["agent:integrate_" + b] = {"obj": {"component_name": "Agent", "params": integrate_params}, "downstream": ["agent:format_" + b], "upstream": ["agent:direct_" + b, "agent:exec_" + b]}
        components["agent:format_" + b] = {"obj": {"component_name": "Agent", "params": format_params}, "downstream": ["eval:" + b], "upstream": ["agent:integrate_" + b]}
        components["eval:" + b] = {"obj": {"component_name": "Agent", "params": eval_params}, "downstream": ["switch:need_improve_" + b], "upstream": ["agent:format_" + b]}
        components["switch:need_improve_" + b] = {"obj": {"component_name": "Switch", "params": need_improve_params}, "downstream": ["assign:final_eval_" + b, "agent:reflect_" + b], "upstream": ["eval:" + b]}
        components["assign:final_eval_" + b] = {"obj": {"component_name": "VariableAssigner", "params": {"variables": [{"variable": "sys.query", "operator": "overwrite", "parameter": "eval:" + b + "@content"}]}}, "downstream": ["msg:" + b], "upstream": ["switch:need_improve_" + b]}
        components["agent:reflect_" + b] = {"obj": {"component_name": "Agent", "params": reflect_params}, "downstream": ["agent:rule_" + b], "upstream": ["switch:need_improve_" + b]}
        components["agent:rule_" + b] = {"obj": {"component_name": "Agent", "params": rule_params}, "downstream": ["agent:memory_update_" + b], "upstream": ["agent:reflect_" + b]}
        components["agent:memory_update_" + b] = {"obj": {"component_name": "Agent", "params": memory_update_params}, "downstream": ["assign:final_update_" + b], "upstream": ["agent:rule_" + b]}
        components["assign:final_update_" + b] = {"obj": {"component_name": "VariableAssigner", "params": {"variables": [{"variable": "sys.query", "operator": "overwrite", "parameter": "agent:memory_update_" + b + "@content"}]}}, "downstream": ["msg:" + b], "upstream": ["agent:memory_update_" + b]}
        components["msg:" + b] = {"obj": {"component_name": "Message", "params": {"stream": False, "content": ["{sys.query}"]}}, "downstream": [], "upstream": ["assign:final_eval_" + b, "assign:final_update_" + b]}

    edges = []

    def add_edge(src: str, tgt: str):
        edges.append(
            {
                "id": f"xy-edge__{_nid(src)}start-{_nid(tgt)}end",
                "source": _nid(src),
                "sourceHandle": "start",
                "target": _nid(tgt),
                "targetHandle": "end",
                "data": {"isHovered": False},
            }
        )

    base_chain = [
        ("begin", "st:input_parse"),
        ("st:input_parse", "agent:intent_classify"),
        ("agent:intent_classify", "agent:entity_extract"),
        ("agent:entity_extract", "agent:memory_hot"),
        ("agent:memory_hot", "agent:memory_warm"),
        ("agent:memory_warm", "switch:intent"),
        ("switch:intent", "st:process"),
        ("switch:intent", "st:learning"),
        ("switch:intent", "switch:precheck_files"),
        ("switch:precheck_files", "list:precheck"),
        ("switch:precheck_files", "msg:precheckneedfile"),
        ("list:precheck", "st:precheck"),
    ]
    for s, t in base_chain:
        add_edge(s, t)

    for b in ["process", "learning", "precheck"]:
        add_edge("st:" + b, "memory:" + b)
        add_edge("memory:" + b, "assign:" + b)
        add_edge("assign:" + b, "agent:knowledge_" + b)
        add_edge("agent:knowledge_" + b, "switch:strategy_" + b)
        add_edge("switch:strategy_" + b, "agent:direct_" + b)
        add_edge("switch:strategy_" + b, "agent:plan_" + b)
        add_edge("agent:plan_" + b, "agent:exec_" + b)
        add_edge("agent:direct_" + b, "agent:integrate_" + b)
        add_edge("agent:exec_" + b, "agent:integrate_" + b)
        add_edge("agent:integrate_" + b, "agent:format_" + b)
        add_edge("agent:format_" + b, "eval:" + b)
        add_edge("eval:" + b, "switch:need_improve_" + b)
        add_edge("switch:need_improve_" + b, "assign:final_eval_" + b)
        add_edge("switch:need_improve_" + b, "agent:reflect_" + b)
        add_edge("agent:reflect_" + b, "agent:rule_" + b)
        add_edge("agent:rule_" + b, "agent:memory_update_" + b)
        add_edge("agent:memory_update_" + b, "assign:final_update_" + b)
        add_edge("assign:final_eval_" + b, "msg:" + b)
        add_edge("assign:final_update_" + b, "msg:" + b)

    nodes = [
        {"id": "begin", "type": "beginNode", "position": {"x": 40, "y": 360}, "sourcePosition": "left", "targetPosition": "right", "data": {"label": "Begin", "name": "begin", "form": begin_params}},
        {"id": "st_input_parse", "type": "ragNode", "position": {"x": 260, "y": 360}, "sourcePosition": "right", "targetPosition": "left", "data": {"label": "StringTransform", "name": "输入预处理", "form": st_input_parse_params}},
        {"id": "agent_intent_classify", "type": "agentNode", "position": {"x": 500, "y": 360}, "sourcePosition": "right", "targetPosition": "left", "data": {"label": "Agent", "name": "意图识别", "form": intent_classify_params}},
        {"id": "agent_entity_extract", "type": "agentNode", "position": {"x": 740, "y": 360}, "sourcePosition": "right", "targetPosition": "left", "data": {"label": "Agent", "name": "实体提取", "form": entity_extract_params}},
        {"id": "agent_memory_hot", "type": "agentNode", "position": {"x": 980, "y": 360}, "sourcePosition": "right", "targetPosition": "left", "data": {"label": "Agent", "name": "热记忆检索", "form": memory_hot_params}},
        {"id": "agent_memory_warm", "type": "agentNode", "position": {"x": 1220, "y": 360}, "sourcePosition": "right", "targetPosition": "left", "data": {"label": "Agent", "name": "温记忆检索", "form": memory_warm_params}},
        {"id": "switch_intent", "type": "switchNode", "position": {"x": 1460, "y": 360}, "sourcePosition": "right", "targetPosition": "left", "data": {"label": "Switch", "name": "意图路由", "form": switch_intent_params}},
        {"id": "switch_precheck_files", "type": "switchNode", "position": {"x": 1700, "y": 680}, "sourcePosition": "right", "targetPosition": "left", "data": {"label": "Switch", "name": "附件检测", "form": switch_precheck_files_params}},
        {"id": "list_precheck", "type": "listOperationsNode", "position": {"x": 1940, "y": 680}, "sourcePosition": "right", "targetPosition": "left", "data": {"label": "ListOperations", "name": "附件抽样", "form": list_precheck_params}},
        {"id": "msg_precheckneedfile", "type": "messageNode", "position": {"x": 1940, "y": 800}, "sourcePosition": "right", "targetPosition": "left", "data": {"label": "Message", "name": "缺附件提示", "form": {"content": ["当前未检测到附件。请上传待预审文件（如NCM、CAPA、风险评估、管理评审记录）后再进行符合性审查。"]}}},
    ]

    for b, meta in branch_desc.items():
        y = meta["y"]
        nodes.extend(
            [
                {"id": _nid("st:" + b), "type": "ragNode", "position": {"x": 1700, "y": y}, "sourcePosition": "right", "targetPosition": "left", "data": {"label": "StringTransform", "name": "输入理解-" + meta["title"], "form": components["st:" + b]["obj"]["params"]}},
                {"id": _nid("memory:" + b), "type": "agentNode", "position": {"x": 1940, "y": y}, "sourcePosition": "right", "targetPosition": "left", "data": {"label": "Agent", "name": "记忆检索-" + meta["title"], "form": components["memory:" + b]["obj"]["params"]}},
                {"id": _nid("assign:" + b), "type": "variableAssignerNode", "position": {"x": 2180, "y": y}, "sourcePosition": "right", "targetPosition": "left", "data": {"label": "VariableAssigner", "name": "记忆注入-" + meta["title"], "form": components["assign:" + b]["obj"]["params"]}},
                {"id": _nid("agent:knowledge_" + b), "type": "agentNode", "position": {"x": 2420, "y": y}, "sourcePosition": "right", "targetPosition": "left", "data": {"label": "Agent", "name": "知识处理-" + meta["title"], "form": components["agent:knowledge_" + b]["obj"]["params"]}},
                {"id": _nid("switch:strategy_" + b), "type": "switchNode", "position": {"x": 2660, "y": y}, "sourcePosition": "right", "targetPosition": "left", "data": {"label": "Switch", "name": "策略选择-" + meta["title"], "form": components["switch:strategy_" + b]["obj"]["params"]}},
                {"id": _nid("agent:direct_" + b), "type": "agentNode", "position": {"x": 2900, "y": y - 70}, "sourcePosition": "right", "targetPosition": "left", "data": {"label": "Agent", "name": "直接回答-" + meta["title"], "form": components["agent:direct_" + b]["obj"]["params"]}},
                {"id": _nid("agent:plan_" + b), "type": "agentNode", "position": {"x": 2900, "y": y + 40}, "sourcePosition": "right", "targetPosition": "left", "data": {"label": "Agent", "name": "工具规划-" + meta["title"], "form": components["agent:plan_" + b]["obj"]["params"]}},
                {"id": _nid("agent:exec_" + b), "type": "agentNode", "position": {"x": 3140, "y": y + 40}, "sourcePosition": "right", "targetPosition": "left", "data": {"label": "Agent", "name": "执行调度-" + meta["title"], "form": components["agent:exec_" + b]["obj"]["params"]}},
                {"id": _nid("agent:integrate_" + b), "type": "agentNode", "position": {"x": 3380, "y": y}, "sourcePosition": "right", "targetPosition": "left", "data": {"label": "Agent", "name": "结果整合-" + meta["title"], "form": components["agent:integrate_" + b]["obj"]["params"]}},
                {"id": _nid("agent:format_" + b), "type": "agentNode", "position": {"x": 3620, "y": y}, "sourcePosition": "right", "targetPosition": "left", "data": {"label": "Agent", "name": "格式优化-" + meta["title"], "form": components["agent:format_" + b]["obj"]["params"]}},
                {"id": _nid("eval:" + b), "type": "agentNode", "position": {"x": 3860, "y": y}, "sourcePosition": "right", "targetPosition": "left", "data": {"label": "Agent", "name": "质量评估-" + meta["title"], "form": components["eval:" + b]["obj"]["params"]}},
                {"id": _nid("switch:need_improve_" + b), "type": "switchNode", "position": {"x": 4100, "y": y}, "sourcePosition": "right", "targetPosition": "left", "data": {"label": "Switch", "name": "是否改进-" + meta["title"], "form": components["switch:need_improve_" + b]["obj"]["params"]}},
                {"id": _nid("assign:final_eval_" + b), "type": "variableAssignerNode", "position": {"x": 4340, "y": y - 70}, "sourcePosition": "right", "targetPosition": "left", "data": {"label": "VariableAssigner", "name": "输出直通-" + meta["title"], "form": components["assign:final_eval_" + b]["obj"]["params"]}},
                {"id": _nid("agent:reflect_" + b), "type": "agentNode", "position": {"x": 4340, "y": y + 40}, "sourcePosition": "right", "targetPosition": "left", "data": {"label": "Agent", "name": "反思分析-" + meta["title"], "form": components["agent:reflect_" + b]["obj"]["params"]}},
                {"id": _nid("agent:rule_" + b), "type": "agentNode", "position": {"x": 4580, "y": y + 40}, "sourcePosition": "right", "targetPosition": "left", "data": {"label": "Agent", "name": "规则提炼-" + meta["title"], "form": components["agent:rule_" + b]["obj"]["params"]}},
                {"id": _nid("agent:memory_update_" + b), "type": "agentNode", "position": {"x": 4820, "y": y + 40}, "sourcePosition": "right", "targetPosition": "left", "data": {"label": "Agent", "name": "记忆更新-" + meta["title"], "form": components["agent:memory_update_" + b]["obj"]["params"]}},
                {"id": _nid("assign:final_update_" + b), "type": "variableAssignerNode", "position": {"x": 5060, "y": y + 40}, "sourcePosition": "right", "targetPosition": "left", "data": {"label": "VariableAssigner", "name": "输出改进-" + meta["title"], "form": components["assign:final_update_" + b]["obj"]["params"]}},
                {"id": _nid("msg:" + b), "type": "messageNode", "position": {"x": 5300, "y": y}, "sourcePosition": "right", "targetPosition": "left", "data": {"label": "Message", "name": "最终输出-" + meta["title"], "form": {"content": ["{sys.query}"]}}},
            ]
        )

    return {
        "components": components,
        "history": [],
        "path": [],
        "retrieval": [],
        "graph": {"edges": edges, "nodes": nodes},
        "globals": {
            "sys.query": "",
            "sys.user_id": "",
            "sys.conversation_turns": 0,
            "sys.files": [],
            "sys.history": [],
            "sys.date": "",
        },
        "variables": [],
    }


def _find_agent_by_title(rag: RAGFlow, title: str):
    try:
        agents = rag.list_agents(page=1, page_size=200, title=title)
    except Exception as e:
        msg = str(e)
        if "doesn't exist" in msg or "does not exist" in msg:
            return None
        raise
    for a in agents:
        if getattr(a, "title", "") == title:
            return a
    return None


def main() -> None:
    api_key = os.getenv("RAGFLOW_API_KEY", "").strip()
    if not api_key:
        raise ValueError("RAGFLOW_API_KEY 未设置")

    base_url = os.getenv("RAGFLOW_BASE_URL", "http://ragflow.local").strip()
    llm_id = os.getenv("QMS_AGENT_LLM_ID", "qwen-plus@Tongyi-Qianwen").strip()
    title = os.getenv("QMS_AGENT_TITLE", "QMS Copilot - 3 Core Flows").strip()
    description = os.getenv(
        "QMS_AGENT_DESC",
        "覆盖流程问答、体系学习、文件预审的QMS智能体（代码自动生成）。",
    ).strip()
    kb_ids = _parse_csv_ids(os.getenv("QMS_DATASET_IDS"))

    rag = RAGFlow(api_key=api_key, base_url=base_url)
    dsl = build_qms_3core_dsl(llm_id=llm_id, kb_ids=kb_ids)

    existing = _find_agent_by_title(rag, title)
    if existing:
        rag.update_agent(existing.id, title=title, description=description, dsl=dsl)
        agent_id = existing.id
        action = "updated"
    else:
        rag.create_agent(title=title, description=description, dsl=dsl)
        created = _find_agent_by_title(rag, title)
        agent_id = created.id if created else "<unknown>"
        action = "created"

    print(
        f"QMS agent {action}: title='{title}', id='{agent_id}', "
        f"kb_count={len(kb_ids)}, llm_id='{llm_id}'"
    )


if __name__ == "__main__":
    main()
