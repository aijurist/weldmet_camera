import matplotlib.pyplot as plt

def draw_and_calculate_weld_depth():
    # Plot initial lines
    plt.figure(figsize=(8, 8))
    plt.xlim(0, 20)
    plt.ylim(0, 20)
    plt.xlabel('X')
    plt.ylabel('Y')
    plt.title('Draw the Horizontal (x) & Vertical (y) Lines of the Weld Configuration')
    plt.grid(True)

    # Get user input for the end points of the horizontal and vertical lines
    x0 = plt.ginput(1, timeout=-1)[0][0]  # Get the 1st mouse click for horizontal line end
    y0 = plt.ginput(1, timeout=-1)[0][1]  # Get the 1st mouse click for vertical line end
    plt.plot([x0[0], x0[0]], [0, y0], 'b-')  # Draw vertical line
    
    plt.ginput(1, timeout=-1)[0]  # Wait for another click to lock in vertical line end
    x1 = plt.ginput(1, timeout=-1)[0][0]  # Get the 2nd mouse click defining the endpoint
    y1 = plt.ginput(1, timeout=-1)[0][1]  # Get the 2nd mouse click defining the endpoint
    plt.plot([0, x1], [y0, y0], 'b-')  # Draw horizontal line
    
    # Initialized variables for calculations
    horizontal_length = x1 - 0
    vertical_length = y0 - 0
    
    # Allow user to draw the "weld curve" manually
    plt.plot([x0[0], x1], [y0, y1], 'g-', label='Weld Curve')
    
    plt.legend()
    
    # Calculate and print the weld depth (assumed to be the min of horizontal & vertical lengths here)
    weld_depth = min(horizontal_length, vertical_length)
    print(f'Weld Configuration drawn. Depth of the weld is: {weld_depth} units.')
    
    plt.show()

# Call the function to start drawing
draw_and_calculate_weld_depth()