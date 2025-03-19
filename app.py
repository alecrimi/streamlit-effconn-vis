import streamlit as st
import numpy as np
from fury import window, actor
from dipy.io.streamline import load_trk
import os
import vtk
import pyvista as pv
from stpyvista import stpyvista

# Function to visualize effective connectivity
def vis_effconn(node_file, edge_file, trk_file_path="HCP.trk", show_tractography=True, show_node_names=False, label_size=2.0):
    """
    Load and visualize a .trk file with AAL90 connectome atlas overlay,
    with static arrows (cones) at the end of each connection.
    """
    # Check if files exist
    for file_path in [trk_file_path, node_file, edge_file]:
        if not os.path.exists(file_path):
            st.error(f"Error: File {file_path} does not exist.")
            return

    # Create a scene
    scene = window.Scene()

    # Initialize streamlines_actor as None
    streamlines_actor = None

    try:
        # Get the center of tracts to align nodes properly
        if show_tractography:
            # Load the tractogram with bbox_valid_check=False to bypass validation
            st.write(f"Loading {trk_file_path}...")
            tractogram = load_trk(trk_file_path, reference="same", bbox_valid_check=False)

            # Get streamlines in their current space
            streamlines = tractogram.streamlines

            # Get the bounding box of the streamlines to help with placement
            all_points = np.vstack([s for s in streamlines])
            min_bounds = np.min(all_points, axis=0)
            max_bounds = np.max(all_points, axis=0)
            center_of_tracts = (min_bounds + max_bounds) / 2
            st.write(f"Tract bounds: Min {min_bounds}, Max {max_bounds}, Center {center_of_tracts}")

            # Add streamlines to the scene with initial opacity
            streamlines_actor = actor.line(streamlines)
            streamlines_actor.GetProperty().SetOpacity(0.1)
            scene.add(streamlines_actor)
        else:
            # If not showing tractography, we'll use a default center or the center of nodes
            center_of_tracts = np.zeros(3)
            st.write("Tractography display disabled.")

        # Load node coordinates
        st.write(f"Loading {node_file}...")

        # Read the node file as plain text and process it manually
        with open(node_file, 'r') as f:
            lines = f.readlines()

        # Initialize lists to store data
        node_coords = []
        node_sizes = []
        node_names = []  # Initialize node_names as an empty list

        # Process each line
        for line in lines:
            try:
                # Split the line by tabs or spaces
                parts = line.strip().split()

                # Ensure the line has at least 6 values (x, y, z, color, size, name)
                if len(parts) < 6:
                    st.write(f"Skipping invalid line (expected at least 6 values): {line.strip()}")
                    continue

                # Process the values: coordinates, color, size, and name
                x = float(parts[0])
                y = float(parts[1])
                z = float(parts[2])
                color = float(parts[3])  # Color is the fourth value (unused in this code)
                size = float(parts[4])  # Size is the fifth value
                name = parts[5]  # Name is the sixth value

                # Append coordinates, size, and name to the respective lists
                node_coords.append([x, y, z])
                node_sizes.append(size)
                node_names.append(name)

            except ValueError as e:
                st.write(f"Skipping invalid line (could not parse coordinates, color, size, or name): {line.strip()}")

        # Convert to numpy arrays
        node_coords = np.array(node_coords, dtype=np.float64)
        node_sizes = np.array(node_sizes, dtype=np.float64)

        # Debugging: Print the number of nodes loaded
        st.write(f"Loaded {len(node_coords)} nodes.")
        if len(node_coords) == 0:
            st.error("Error: No valid nodes were loaded. Check the node file format.")
            return  # Exit the function if no nodes are loaded

        # Ensure exactly 90 nodes are loaded for AAL90 atlas
        if len(node_coords) != 90:
            st.error(f"Error: Expected 90 nodes (AAL90 atlas), but found {len(node_coords)} nodes.")
            return

        # Calculate the bounding box and center of the connectome
        min_node_bounds = np.min(node_coords, axis=0)
        max_node_bounds = np.max(node_coords, axis=0)
        center_of_nodes = (min_node_bounds + max_node_bounds) / 2
        st.write(f"Node bounds: Min {min_node_bounds}, Max {max_node_bounds}, Center {center_of_nodes}")

        # If we don't have tractography and thus no center_of_tracts, use the nodes center
        if not show_tractography:
            center_of_tracts = center_of_nodes

        # Calculate the translation needed to align centers
        translation = center_of_tracts - center_of_nodes
        st.write(f"Translation vector: {translation}")

        # Apply translation to node coordinates
        adjusted_node_coords = node_coords + translation

        # Load connectivity matrix
        st.write(f"Loading {edge_file}...")
        connectivity_matrix = np.loadtxt(edge_file)

        # Ensure the connectivity matrix is 90x90
        if connectivity_matrix.shape != (90, 90):
            st.error(f"Error: Connectivity matrix must be 90x90 for AAL90 atlas, but found shape {connectivity_matrix.shape}.")
            return

        # Create edges/connections between nodes with arrows
        i_indices, j_indices = np.where(connectivity_matrix > 0)

        # Function to create a cone at the end of a connection
        def create_arrow_cone(start_point, end_point, color=(0.0, 0.7, 0.2)):
            # Calculate direction vector
            direction = end_point - start_point
            direction_length = np.linalg.norm(direction)

            if direction_length < 1e-6:
                return None  # Skip if points are too close

            direction_norm = direction / direction_length

            # Create a cone positioned near the end point (95% along the connection)
            cone_position = start_point + 0.95 * direction

            # Scale cone size based on the connection length
            cone_height = min(5.0, max(2.0, direction_length * 0.1))
            cone_radius = cone_height * 0.3

            # Create cone source
            cone = vtk.vtkConeSource()
            cone.SetHeight(cone_height)
            cone.SetRadius(cone_radius)
            cone.SetResolution(12)
            cone.SetDirection(direction_norm)
            cone.SetCenter(cone_position)
            cone.Update()

            # Create mapper
            cone_mapper = vtk.vtkPolyDataMapper()
            cone_mapper.SetInputData(cone.GetOutput())

            # Create actor
            cone_actor = vtk.vtkActor()
            cone_actor.SetMapper(cone_mapper)
            cone_actor.GetProperty().SetColor(color)

            return cone_actor

        # Create containers for different elements of the visualization
        node_assembly = vtk.vtkAssembly()
        edge_assembly = vtk.vtkAssembly()
        arrow_assembly = vtk.vtkAssembly()
        label_assembly = vtk.vtkAssembly()  # New assembly for labels

        # Lists to store actors for opacity control
        node_actors = []
        edge_actors = []
        arrow_actors = []
        label_actors = []  # New list for label actors

        # Function to orient labels horizontally always facing the camera
        def create_billboard_text(text, position, size):
            # Create a text source
            text_source = vtk.vtkVectorText()
            text_source.SetText(text)
            text_source.Update()
            
            # Create a mapper for the text
            text_mapper = vtk.vtkPolyDataMapper()
            text_mapper.SetInputConnection(text_source.GetOutputPort())
            
            # Create follower actor (always faces camera)
            text_actor = vtk.vtkFollower()
            text_actor.SetMapper(text_mapper)
            text_actor.SetPosition(position)
            text_actor.SetScale(size, size, size)
            text_actor.GetProperty().SetColor(1.0, 1.0, 1.0)  # White color
            
            # Rotate the text 90 degrees 
            text_actor.RotateX(90)
            text_actor.RotateY(180)
            
            return text_actor

        # Create manual spheres for nodes
        for i, (coord, size, name) in enumerate(zip(adjusted_node_coords, node_sizes, node_names)):
            # Create sphere source
            sphere_source = vtk.vtkSphereSource()
            sphere_source.SetCenter(coord[0], coord[1], coord[2])
            sphere_source.SetRadius(size * 0.9)  # Scale size for better visibility
            sphere_source.SetPhiResolution(12)
            sphere_source.SetThetaResolution(12)
            sphere_source.Update()

            # Create mapper
            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputData(sphere_source.GetOutput())

            # Create actor
            sphere_actor = vtk.vtkActor()
            sphere_actor.SetMapper(mapper)
            sphere_actor.GetProperty().SetColor(0.8, 0.3, 0.3)  # Red color

            # Add to scene
            scene.add(sphere_actor)
            node_assembly.AddPart(sphere_actor)
            node_actors.append(sphere_actor)

            # Add node name labels if enabled
            if show_node_names:
                # Position slightly offset from node
                label_position = [
                    coord[0] + size * 1.2,
                    coord[1] + size * 1.2,
                    coord[2] + size * 1.2
                ]
                
                # Create billboard text that will always face the camera and be horizontal
                text_actor = create_billboard_text(name, label_position, label_size)
                
                # Add to scene
                scene.add(text_actor)
                label_assembly.AddPart(text_actor)
                label_actors.append(text_actor)

        # Create edges and arrows
        for i, j in zip(i_indices, j_indices):
            # Ensure node indices are within bounds
            if i >= len(adjusted_node_coords) or j >= len(adjusted_node_coords):
                st.write(f"Skipping invalid connection: node indices ({i}, {j}) are out of bounds.")
                continue

            start_point = adjusted_node_coords[i]
            end_point = adjusted_node_coords[j]

            # Create line source
            line_source = vtk.vtkLineSource()
            line_source.SetPoint1(start_point[0], start_point[1], start_point[2])
            line_source.SetPoint2(end_point[0], end_point[1], end_point[2])
            line_source.Update()

            # Create mapper
            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputData(line_source.GetOutput())

            # Create actor
            line_actor = vtk.vtkActor()
            line_actor.SetMapper(mapper)
            line_actor.GetProperty().SetColor(0.2, 0.2, 0.8)  # Blue color
            line_actor.GetProperty().SetLineWidth(2.0)

            # Add to scene
            scene.add(line_actor)
            edge_assembly.AddPart(line_actor)
            edge_actors.append(line_actor)

            # Add arrow at the end of the connection (from i to j)
            arrow_actor = create_arrow_cone(start_point, end_point)
            if arrow_actor:
                scene.add(arrow_actor)
                arrow_assembly.AddPart(arrow_actor)
                arrow_actors.append(arrow_actor)

        # Add the assemblies to the scene
        scene.add(node_assembly)
        scene.add(edge_assembly)
        scene.add(arrow_assembly)
        if show_node_names:
            scene.add(label_assembly)

        # Add orientation markers
        scene.add(actor.axes())

        # Reset camera
        scene.reset_camera()
        camera = scene.set_camera(position=(0, -500, 0),
                                 focal_point=(0, 0, 0),
                                 view_up=(0, 0, 1))
        
        # Set the camera for all followers (the text labels)
        for text_actor in label_actors:
            text_actor.SetCamera(camera)

        # Convert the FURY scene to a PyVista plotter
        plotter = pv.Plotter()
        plotter.add_actor(scene)

        # Display the plotter in Streamlit
        stpyvista(plotter)

    except Exception as e:
        st.error(f"Error loading or processing data: {e}")
        import traceback
        traceback.print_exc()

# Streamlit app
st.title("Effective Connectivity Visualization")
st.write("Visualize tractography and AAL90 connectome atlas overlay.")

# File uploaders
trk_file = st.file_uploader("Upload .trk file", type=["trk"])
node_file = st.file_uploader("Upload node file", type=["node"])
edge_file = st.file_uploader("Upload edge file", type=["edge"])

# Options
show_tractography = st.checkbox("Show Tractography", value=True)
show_node_names = st.checkbox("Show Node Names", value=False)
label_size = st.slider("Label Size", min_value=1.0, max_value=5.0, value=2.0)

# Run visualization
if st.button("Visualize"):
    if trk_file and node_file and edge_file:
        # Save uploaded files to temporary paths
        trk_file_path = "temp.trk"
        node_file_path = "temp.node"
        edge_file_path = "temp.edge"

        with open(trk_file_path, "wb") as f:
            f.write(trk_file.getbuffer())
        with open(node_file_path, "wb") as f:
            f.write(node_file.getbuffer())
        with open(edge_file_path, "wb") as f:
            f.write(edge_file.getbuffer())

        # Call the visualization function
        vis_effconn(node_file_path, edge_file_path, trk_file_path, show_tractography, show_node_names, label_size)
    else:
        st.error("Please upload all required files.")