import asyncio
import json
import os
import sys


def test_real_stdio_mcp_exposes_only_six_guarded_tools_without_starting_worker(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    async def scenario():
        params=StdioServerParameters(command=sys.executable,args=['-m','xiaomao.mcp_server','--root',str(tmp_path)],env={**os.environ,'PYTHONIOENCODING':'utf-8'})
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                tools=await session.list_tools()
                assert {tool.name for tool in tools.tools} == {
                    'xiaomao_status','xiaomao_start','xiaomao_enable_auto_reply',
                    'xiaomao_pause','xiaomao_pending','xiaomao_stop'}
                enable=next(tool for tool in tools.tools if tool.name=='xiaomao_enable_auto_reply')
                assert enable.model_dump(by_alias=True)['annotations']['openWorldHint'] is True
                start=next(tool for tool in tools.tools if tool.name=='xiaomao_start')
                assert start.model_dump(by_alias=True)['annotations']['openWorldHint'] is True
                result=await session.call_tool('xiaomao_status',{})
                data=result.model_dump(by_alias=True)
                assert not data.get('isError')
                content=data.get('structuredContent') or json.loads(data['content'][0]['text'])
                assert content['running'] is False
        assert not (tmp_path/'runtime'/'control.json').exists()
    asyncio.run(scenario())
