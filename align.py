import numpy as np
import cv2
import os
import argparse

def split_helper(img: np.ndarray):
    """
    Splits the image into three equal parts and returns the three parts.

    Args:
    - img: 2D array representing the image.

    Returns:
    - B: 2D array representing the top third of the image.
    - G: 2D array representing the middle third of the image.
    - R: 2D array representing the bottom third of the image.
    """

    assert (len(img.shape)==2)

    # Split the image into three equal parts

    return B, G, R

def align_images(r: np.ndarray, g: np.ndarray, b: np.ndarray) -> np.ndarray:
    """
    Aligns the provided RGB channels of an image based on feature-based image registration.
    
    Args:
    - r: 2D array representing the Red channel of the image.
    - g: 2D array representing the Green channel of the image (reference channel).
    - b: 2D array representing the Blue channel of the image.

    Returns:
    - aligned_image: 3D array representing the aligned RGB image.

    Workflow:
    1. Feature Detection and Matching:
        a. Detect keypoints and compute the descriptors using the provided feature detector (ORB).
        b. Match the descriptors between the channel and the reference channel g.
        c. Sort the matches based on match quality/distance.

    2. Estimate Transformation:
        a. Estimate the transformation (e.g., translation, rotation) that aligns the detected keypoints between the channel and the reference channel g.
           Use a method such as RANSAC for robust estimation.

    3. Apply Transformation:
        a. Use the obtained transformation matrix to warp the channels r and b to align them with the reference channel g.

    4. Merge Channels:
        a. Combine the aligned r, g, and b channels to create the resultant aligned RGB image.
    """

    assert (r.shape == g.shape == b.shape)

    # Initialize ORB detector


    # Matcher


    def align_channel(channel, ref_channel):
        """
        Helper function to align a channel

        Args:
        - channel :  Candidate channel (Example R and B)
        - ref_channel : Reference channel (Example G)

        Returns:
        - aligned_channel: 3D array representing the aligned channel
        """
        # Find keypoints and descriptors

        
        # Match descriptors

        
        # Extract location of good matches

        
        # Find the transformation matrix


        # Warp the channel using the transformation matrix

        pass


    # Use green channel as reference and align


    # Merge the channels

    assert (aligned_img.shape == (g.shape[0], g.shape[1], 3) == (r.shape[0], r.shape[1], 3) == (b.shape[0], b.shape[1], 3))

    # Return aligned image

    pass


if __name__ == '__main__':
    """
    Feel free to test your function here.
    """
    # B, G, R = split_helper(image)
    pass