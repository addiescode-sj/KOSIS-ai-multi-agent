"""서비스 응답(ChatResult) 변환: direct 턴과 계산용 다듬기 턴. 오프라인."""

from kosis_agent.graph.state import new_checkpointer
from kosis_multi.agents.judge import DIRECT_FALLBACK
from kosis_multi.agents.rewrite import RewriteReply
from kosis_multi.orchestrator import build_graph
from kosis_multi.result import multi_chat_result
from tests.test_workflow import STAT_LLM, Search, Stat, answered


def invoke(graph, question, thread="t"):
    return graph.invoke({"messages": [("user", question)]}, config={"configurable": {"thread_id": thread}})


def test_a_direct_first_turn_has_no_search_and_no_table():
    graph = build_graph(Search(["101_A"]), STAT_LLM, summarize=Stat({}))
    result = multi_chat_result(invoke(graph, "안녕하세요"))
    assert result.route == "direct" and result.answer_origin == "direct" and result.answer == DIRECT_FALLBACK
    assert not result.no_answer and result.candidates == [] and result.table_key is None and result.chart is None


def test_a_direct_turn_does_not_show_the_previous_search():
    graph = build_graph(Search(["101_A"]), STAT_LLM, summarize=Stat({"101_A": answered("101_A")}),
                        checkpointer=new_checkpointer())
    invoke(graph, "2024년 서울 출생아 수")
    result = multi_chat_result(invoke(graph, "고마워요"))
    assert result.route == "direct" and result.picked == [] and result.stat_answer is None


def test_a_rewritten_turn_shows_the_note_and_the_sentence_given_to_stage_1():
    rewrite = lambda q: RewriteReply(reason="작년 = 2025년", rewrite="2025년 서울 출생아수")  # noqa: E731
    graph = build_graph(Search(["101_A"]), STAT_LLM, summarize=Stat({"101_A": answered("101_A")}), rewrite_llm=rewrite)
    result = multi_chat_result(invoke(graph, "작년 서울 출생아 수"))
    assert result.answer_origin == "stat" and result.answer.endswith("읽고 답했어요.)")
    assert result.resolved_question == "2025년 서울 출생아수" and result.question == "작년 서울 출생아 수"


def test_other_turns_match_the_existing_result():
    from kosis_agent.graph.state import chat_result

    graph = build_graph(Search(["101_A"]), STAT_LLM, summarize=Stat({"101_A": answered("101_A")}))
    state = invoke(graph, "2024년 서울 출생아 수")
    assert multi_chat_result(state) == chat_result(state)
