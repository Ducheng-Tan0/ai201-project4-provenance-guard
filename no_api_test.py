# Run the test script with LLM disabled (no API call)

from signals import compute_stylometric_score
tests = [
    ('AI text',      'Artificial intelligence represents a transformative paradigm shift in modern society. It is important to note that while the benefits of AI are numerous, it is equally essential to consider the ethical implications. Furthermore, stakeholders across various sectors must collaborate to ensure responsible deployment.'),
    ('Human text',   'ok so i finally tried that new ramen place downtown and honestly? underwhelming. the broth was fine but they put WAY too much sodium in it and i was thirsty for like three hours after. my friend got the spicy version and said it was better. probably will not go back unless someone drags me there'),
]
for label, text in tests:
    score = compute_stylometric_score(text)
    print(f'{label}: stylo_score = {score:.4f}')


from dotenv import load_dotenv
load_dotenv()
from signals import classify_with_llm
score = classify_with_llm('ok so i finally tried that new ramen place downtown and honestly? underwhelming.')
print(f'llm_score = {score:.4f}')