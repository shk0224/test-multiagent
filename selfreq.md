# WRAPPER FUNCTION

A wrapper is a normal Python function that calls
another function and may perform extra processing.

### Example

```python
def calculate(a, b):
    result = add(a, b)
    return result
```

**Important:**
- `add()` = Original Function
- `calculate()` = Wrapper Function
- Wrapper is not a Python keyword.