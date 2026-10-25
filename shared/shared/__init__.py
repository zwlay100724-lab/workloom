"""TaskFlow / DocMind 共用的基础设施。

约定(很重要,别破坏):
1. shared 只装"纯组件",不装任何 app 的配置实例/单例。
   谁用 api_key?用哪个模型?——都是 app 的事,由 app 往组件里注入自己的 settings。
2. shared 绝不 import 任何 `app.xxx`(taskflow / docmind 的业务包)。
   依赖只能往下流:app → shared。
3. 每个 app 都 `pip install -e ../shared`,所以改动 shared 即时生效、两处共享。
"""