import asyncio
import textwrap
import os
import sys

try:
    from openai import AsyncOpenAI
except ImportError:
    print("Please install openai: pip install openai")
    sys.exit(1)

from ultrastealth import UltrastealthFetcher

PROMPT = """You are a Playwright automation assistant. 
The user will give you a task. Provide ONLY valid Python async Playwright code to accomplish this task using the existing `page` variable.
You can also use `context` or `fetcher` if needed, but primarily use `page`.
Do not include markdown formatting like ```python. Just the raw code.
Print the results using `print()` if the user asks you to extract something.
Make sure to always `await` asynchronous Playwright methods!

Example interaction:
User: "go to google and search for cats"
Code:
await page.goto("https://google.com")
await page.locator("textarea[title='Search']").fill("cats")
await page.keyboard.press("Enter")
await page.wait_for_load_state("networkidle")
print("Search submitted!")
"""

async def run_llm_repl():
    if not os.getenv("OPENAI_API_KEY"):
        print("Error: OPENAI_API_KEY environment variable is not set!")
        print("Please set it before running the agent: export OPENAI_API_KEY=your-key")
        return
        
    client = AsyncOpenAI()
    
    print("Starting Ultrastealth headed browser...")
    fetcher = UltrastealthFetcher(headless=False)
    await fetcher.start()
    page = await fetcher._context.new_page()
    context = fetcher._context
    
    # Start on a test page
    await page.goto("https://bot.sannysoft.com/")
    
    print("\n" + "="*60)
    print(" Ultrastealth LLM Agent REPL")
    print("="*60)
    print("Type a natural language instruction and the LLM will execute it on the browser.")
    print("Example: 'go to google and search for latest AI news'")
    print("Type 'quit' or 'exit' to close.")
    print("="*60 + "\n")
    
    while True:
        try:
            cmd = input("\nAgent> ")
            if cmd.lower() in ['quit', 'exit']:
                break
            if not cmd.strip():
                continue
                
            print("Thinking...")
            response = await client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": PROMPT},
                    {"role": "user", "content": cmd}
                ],
                temperature=0.0
            )
            code = response.choices[0].message.content.strip()
            
            # Clean up markdown if the LLM includes it
            if code.startswith("```python"): code = code[9:]
            if code.startswith("```"): code = code[3:]
            if code.endswith("```"): code = code[:-3]
            code = code.strip()
            
            print(f"-- Executing Playwright Code: --\n{code}\n--------------------------------")
            
            # Wrap in an async function to allow 'await' and execute dynamically
            wrapped = f"async def __agent_code(page, context, fetcher):\n{textwrap.indent(code, '    ')}"
            local_vars = {}
            exec(wrapped, globals(), local_vars)
            await local_vars['__agent_code'](page, context, fetcher)
            
        except KeyboardInterrupt:
            print("\nKeyboardInterrupt. Type 'quit' to exit.")
        except Exception as e:
            print(f"Error executing LLM code: {e}")

    print("Closing browser...")
    await fetcher.close()

if __name__ == "__main__":
    asyncio.run(run_llm_repl())
