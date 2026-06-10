import asyncio
import os
import IPython
import nest_asyncio
from ultrastealth import UltrastealthFetcher

# Allows IPython's event loop to nest inside asyncio.run()
nest_asyncio.apply()

async def main():
    print("Starting Ultrastealth REPL...")
    print("Initializing headed browser...")
    
    fetcher = UltrastealthFetcher(headless=False)
    await fetcher.start()
    
    context = fetcher._context
    page = await context.new_page()
    await page.goto("https://bot.sannysoft.com/")
    
    print("\n" + "="*60)
    print(" Ultrastealth Interactive Browser REPL")
    print("="*60)
    print("Variables available in this scope:")
    print("  page    : The active Playwright page (try: await page.title())")
    print("  context : The browser context")
    print("  fetcher : The UltrastealthFetcher instance")
    print("\nExample commands to try:")
    print("  await page.goto('https://nowsecure.nl')")
    print("  print(await page.content())")
    print("  await page.locator('button').click()")
    print("="*60 + "\n")
    
    # Start the IPython REPL
    IPython.embed(colors="neutral", using="asyncio")
    
    print("Closing browser...")
    await fetcher.close()

if __name__ == "__main__":
    asyncio.run(main())
