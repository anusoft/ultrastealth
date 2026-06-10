import asyncio
from ultrastealth import UltrastealthFetcher

async def fetch_example():
    print("Starting Ultrastealth...")
    # headless=False to observe the execution
    async with UltrastealthFetcher(headless=True) as us:
        print("Navigating to https://bot.sannysoft.com/")
        # You can execute JS to extract results
        result = await us.fetch_and_evaluate(
            url="https://bot.sannysoft.com/",
            js_expression="() => document.title",
            wait_secs=3.0
        )
        print("Page Title:", result)

if __name__ == "__main__":
    asyncio.run(fetch_example())
