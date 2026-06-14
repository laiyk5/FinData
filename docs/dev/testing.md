# Testing

API access and time are valuable, so before you call the API, do thorough test to make sure you won't trigger the API limit by accidently frequent calls.

One good practice is to do it progressively:

1. run unitest first, if you don't pass unitest, you won't pass larger scope test.
2. then run a smoke test, see if anything go wrong. This reveals a lot of mistakes before you consume vital resources.
3. After all tests passed, starts with a small range and see if anything go wrong.
4. If small range tests passed, usually it means it's ready for real-world scale run. Run and check if anything go wrong after the run interrupted or stopped.
