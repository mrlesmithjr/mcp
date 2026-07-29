---
tags: [tech, python, programming, scripting]
---
# Python Programming

## Language Characteristics

Python is a dynamically typed, interpreted, high-level general-purpose language with a focus on
readability. Indentation defines code blocks instead of curly braces. Python supports multiple
programming paradigms: procedural, object-oriented, and functional. The CPython reference
implementation executes code by compiling to bytecode and running it in the Python Virtual Machine.

## Data Structures

Python provides built-in data structures: lists (ordered, mutable sequences), tuples (immutable
sequences), dictionaries (hash maps with arbitrary keys), and sets (unordered collections of
unique elements). List comprehensions and generator expressions provide concise syntax for
transforming and filtering sequences. The collections module provides specialized containers
like deque, OrderedDict, and Counter.

## Classes and Object-Oriented Programming

Classes encapsulate data and behavior. __init__ is the constructor; __str__ and __repr__
control string representation. Python supports single inheritance and multiple inheritance via
the method resolution order (MRO). Dunder (double-underscore) methods implement operator
overloading and protocol interfaces like iteration (__iter__, __next__) and context managers
(__enter__, __exit__).

## Type Hints and Static Analysis

PEP 484 introduced type hints for function signatures and variable annotations. The typing
module provides generic types. Mypy performs static type checking without executing the code.
Type hints do not affect runtime behavior but enable IDE tooling and catch bugs before testing.

## Async and Concurrency

The asyncio library implements cooperative multitasking using coroutines defined with async def.
Await suspends execution until an awaitable completes. asyncio.gather runs multiple coroutines
concurrently. For CPU-bound work, multiprocessing bypasses the GIL. ThreadPoolExecutor handles
I/O-bound concurrency in threaded code.
