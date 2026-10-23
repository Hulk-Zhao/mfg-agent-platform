# 依赖精简过滤器：剔除 torch 生态链，为无 GPU 轻量云减负
#
# 用法：awk -f filter-heavy.awk req-full.txt > req.txt
#
# 【为什么不用 RS="" 段落模式】
# uv export 输出的 requirements.txt 实测没有任何空行（711 行全是内容），
# RS="" 会把整份文件当成一条记录，只判断首行就放行全部 —— 过滤器形同虚设。
# 因此改成逐行状态机：注释行跟随上一条包行的去留。
#
# 【黑名单里为什么不止 torch】
# sentence-transformers / transformers / accelerate / peft / milvus-model
# 这些包的 metadata 都声明了 torch 依赖，pip 解析时会把 torch 连同
# 17 个 nvidia CUDA 13 包重新拉回来，只过滤 torch 行等于没过滤。
#
# 【安全性依据】
# 全仓库只有 common/rag_tools.py:142 和 :157 出现 import torch，
# 且都位于 VECTOR_BACKEND == "milvus" 分支内，chroma 模式不会执行到。

BEGIN {
  split("torch triton sentence-transformers flagembedding milvus-model pymilvus accelerate peft transformers datasets", exact, " ")
  for (i in exact) bad_exact[exact[i]] = 1

  npx = split("nvidia- cuda-", prefix, " ")
}

{
  gsub(/\r/, "", $0)          # Windows 产出的文件可能带 CR

  # 注释行与空行：跟从上一条包行的去留
  if ($0 ~ /^[[:space:]]*#/ || $0 ~ /^[[:space:]]*$/) {
    if (!skipping) print
    next
  }

  # 包行：剥离版本号、环境标记、extras 语法后取包名
  name = $0
  sub(/[=;<>=!~ \t[(].*/, "", name)

  skipping = 0
  if (name in bad_exact) { skipping = 1; next }
  for (p = 1; p <= npx; p++)
    if (index(name, prefix[p]) == 1) { skipping = 1; next }

  print
}
