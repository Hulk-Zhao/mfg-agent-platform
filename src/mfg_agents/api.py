"""FastAPI 后端：直接顺序调用Agent，完全控制事件推送。"""
import json
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .common.business import business_agent
from .agents.manufacturing import ManufacturingAgent
from .agents.quality import quality_agent
from .agents.supply_chain import supply_chain_agent
from .agents.equipment import equipment_agent
from .agents.fa import fa_agent
from .agents.report import report_agent
from .common.llm_factory import get_llm

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时预热模型到显存
    async def warmup():
        try:
            llm = get_llm(temperature=0.0)
            print("[warmup] 正在预热LLM模型...")
            await asyncio.to_thread(llm.invoke, "回复OK")
            print("[warmup] 模型已加载到显存")
        except Exception as e:
            print(f"[warmup] 预热失败（首次调用时会自动加载）: {e}")
    asyncio.create_task(warmup())
    yield


app = FastAPI(title="mfg-agent-platform", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class ChatRequest(BaseModel):
    query: str


def emit(ev: dict) -> str:
    return json.dumps(ev, ensure_ascii=False) + "\n"


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/chat/stream")
def chat_stream(req: ChatRequest):
    def gen():
        state: dict = {"user_query": req.query}
        mfg = ManufacturingAgent()

        try:
            # 1. 业务Agent
            yield emit({"type": "agent_start", "node": "business", "label": "业务Agent", "icon": "🧠"})
            state.update(business_agent(state))
            yield emit({"type": "agent_end", "node": "business", "label": "业务Agent",
                        "summary": f"识别场景：{state.get('scenario')} | 型号：{state.get('product_model')} | 线体：{state.get('line_code')}"})

            # 2. 制造Agent
            yield emit({"type": "agent_start", "node": "manufacturing", "label": "制造Agent · 查MES", "icon": "🗄️"})
            yield emit({"type": "tool_start", "tool": "SQL查询", "detail": "查询MES：OEE、产出、停线事件"})
            state.update(mfg.run(state))
            oee_rows = state.get("oee_data", {}).get("rows", [])
            yield emit({"type": "tool_end", "tool": "SQL查询",
                        "detail": f"返回{len(oee_rows)}条产出记录"})
            yield emit({"type": "agent_end", "node": "manufacturing", "label": "制造Agent · 查MES",
                        "summary": f"OEE={oee_rows[-1].get('oee') if oee_rows else '?'}% | 停线确认={state.get('confirmed_stop')}"})

            if not state.get("confirmed_stop"):
                yield emit({"type": "final", "report": "经核查，当前产线运行正常，无停线情况。", "internal": state})
                return

            # 3. 并行三Agent
            yield emit({"type": "agent_start", "node": "quality", "label": "品质Agent · 查QMS", "icon": "🔬"})
            yield emit({"type": "tool_start", "tool": "SQL查询", "detail": "查询QMS不良分布"})
            state.update(quality_agent(state))
            yield emit({"type": "tool_end", "tool": "SQL查询", "detail": f"返回{len(state.get('quality_data',{}).get('rows',[]))}种不良"})
            yield emit({"type": "agent_end", "node": "quality", "label": "品质Agent · 查QMS",
                        "summary": state.get("quality_summary", "")})

            yield emit({"type": "agent_start", "node": "supply_chain", "label": "供应链Agent · 查SCM", "icon": "📦"})
            yield emit({"type": "tool_start", "tool": "SQL查询", "detail": "查询SCM来料批次"})
            state.update(supply_chain_agent(state))
            yield emit({"type": "tool_end", "tool": "SQL查询", "detail": f"返回{len(state.get('supply_data',{}).get('rows',[]))}条批次"})
            yield emit({"type": "agent_end", "node": "supply_chain", "label": "供应链Agent · 查SCM",
                        "summary": state.get("supply_summary", "")})

            yield emit({"type": "agent_start", "node": "equipment", "label": "设备Agent · 查EAM", "icon": "⚙️"})
            yield emit({"type": "tool_start", "tool": "SQL查询", "detail": "查询EAM设备异常"})
            state.update(equipment_agent(state))
            yield emit({"type": "tool_end", "tool": "SQL查询", "detail": f"返回{len(state.get('equipment_data',{}).get('rows',[]))}条异常"})
            yield emit({"type": "agent_end", "node": "equipment", "label": "设备Agent · 查EAM",
                        "summary": state.get("equipment_summary", "")})

            # 4. FA Agent
            yield emit({"type": "agent_start", "node": "fa", "label": "FA分析Agent · RAG推理", "icon": "📚"})
            yield emit({"type": "tool_start", "tool": "RAG检索", "detail": "匹配历史异常案例库"})
            yield emit({"type": "llm_start", "detail": "FA模型推理中（评级/根因/复线时间）..."})
            state.update(fa_agent(state))
            yield emit({"type": "agent_end", "node": "fa", "label": "FA分析Agent · RAG推理",
                        "summary": state.get("fa_summary", "")})

            # 5. 报告Agent
            yield emit({"type": "agent_start", "node": "report", "label": "报告Agent · 生成对客回复", "icon": "📄"})
            yield emit({"type": "llm_start", "detail": "生成对客脱敏报告..."})
            state.update(report_agent(state))
            yield emit({"type": "agent_end", "node": "report", "label": "报告Agent · 生成对客回复",
                        "summary": "对客报告已生成"})

            yield emit({"type": "final",
                        "report": state.get("customer_report", ""),
                        "internal": state.get("internal_brief", {}),
                        "fa_summary": state.get("fa_summary", "")})
        except Exception as e:
            yield emit({"type": "error", "message": str(e)})

    return StreamingResponse(gen(), media_type="application/x-ndjson")
