# Todo App

A simple, clean todo list application with persistent storage.

## Description

A single-page todo application where users can add, complete, and delete tasks. Tasks persist across page reloads using localStorage. The UI should be minimal and responsive.

## Requirements

- Users can add a new todo by typing in an input field and pressing Enter or clicking Add
- Each todo displays its text and a checkbox to mark complete
- Completed todos show strikethrough text styling
- Users can delete individual todos with a delete button
- Todos persist in localStorage across page reloads
- Input field clears after adding a todo
- Empty todos cannot be added (whitespace-only counts as empty)
- Show a count of remaining (incomplete) todos

## Acceptance Criteria

- Page loads with any previously saved todos visible
- Adding a todo immediately appears in the list
- Checking a todo toggles its completed state and persists
- Deleting a todo removes it from the list and localStorage
- The remaining count updates in real time
- Works on mobile viewports (320px+)
- Keyboard accessible (Tab, Enter, Space work as expected)

## UI Description

Clean white background, centered container (max-width 600px). Input field at top with an "Add" button. Todo list below with checkboxes on the left, text in the middle, delete (X) button on the right. Completed items have muted text with strikethrough. Footer shows "X items left".

## User Flows

### Add a todo
1. Navigate to "/"
2. Click on the input field
3. Type "Buy groceries" in the "input" field
4. Click "Add"
5. Should see "Buy groceries" in the list

### Complete a todo
1. Navigate to "/"
2. Click on the checkbox next to "Buy groceries"
3. Should see strikethrough text on "Buy groceries"

### Delete a todo
1. Navigate to "/"
2. Click "X" button next to "Buy groceries"
3. Should not see "Buy groceries" in the list
