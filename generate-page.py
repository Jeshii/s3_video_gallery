from jinja2 import Environment, FileSystemLoader
import boto3
from urllib.parse import urlparse
import os
import configparser
import subprocess
import mimetypes

#inital varibles
config = configparser.ConfigParser()

if os.path.exists('config.ini'):
    config.read('config.ini')
else:
    # Prompt the user to input the values
    templates_folder = input('Enter you templates folder: ')
    bucket_name = input('Enter your s3 bucket name: ')
    s3_region = input('Enter your s3 bucket region: ')
    local_video_path = input('Enter the local location of the videos: ')

    # Create the configuration file and store the values
    config['Environment'] = {
        'TEMPLATES_FOLDER': templates_folder,
        'BUCKET_NAME': bucket_name,
        'S3_REGION': s3_region,
        'LOCAL_VIDEO_PATH': local_video_path
    }

    with open('config.ini', 'w') as config_file:
        config.write(config_file)

templates_folder = config.get('Environment', 'TEMPLATES_FOLDER', fallback=os.getenv('TEMPLATES_FOLDER'))

bucket_name = config.get('Environment', 'BUCKET_NAME', fallback=os.getenv('BUCKET_NAME'))

s3_region = config.get('Environment', 'S3_REGION', fallback=os.getenv('S3_REGION'))

local_video_path = config.get('Environment', 'LOCAL_VIDEO_PATH', fallback=os.getenv('LOCAL_VIDEO_PATH'))

supported_extensions = ['MP4', 'MOV']

index_doc = 'index.html'

# Create a Jinja2 environment
env = Environment(loader=FileSystemLoader(templates_folder))

# Create an S3 client
s3_client = boto3.client('s3')

def generate_video_pages(s3_client, bucket_name, video, templates_dir):

    env = Environment(loader=FileSystemLoader(templates_dir))
    video_template = env.get_template('video.html')

    rendered_html = video_template.render(
        title=video['title'],
        width=video['width'],
        height=video['height'],
        video_url=video['video_url'],
        mime_type=video['mime_type']
    )

    # Save the rendered HTML to a separate file for each video
    page_filename = 'pages/' + video['page_name'] + '.html'
    
    #s3_client.put_object(Body=rendered_html, Bucket=bucket_name, Key=page_filename)
    s3_put(s3_client, rendered_html, bucket_name, page_filename)

def generate_index_page(s3_client, bucket_name, videos, templates_dir):
    env = Environment(loader=FileSystemLoader(templates_dir))
    index_template = env.get_template('index.html')

    rendered_html = index_template.render(videos=videos)

    s3_put(s3_client, rendered_html, bucket_name, index_doc)

def s3_put(s3_client, body, bucket, key):
    s3_client.put_object(
        Body=body, 
        Bucket=bucket, 
        Key=key,
        ContentType='text/html')

    response = s3_client.put_object_acl(
        ACL='public-read',
        Bucket=bucket_name,
        Key=key
    )

    if response['ResponseMetadata']['HTTPStatusCode'] == 200:
        print(f"Object {key} in bucket {bucket_name} is now public.")

def update_video_metadata(s3_client, bucket_name, object_key, local_video_path):
    print(f"Working on {local_video_path}")
    # Get the file extension
    file_extension = os.path.splitext(local_video_path)[1].lower()

    # Check if the file extension is supported (mp4 or mov)
    supported_extensions = ['.mp4', '.mov']
    if file_extension not in supported_extensions:
        print("Unsupported file extension: {}".format(file_extension))
        return
    
    # Run ffprobe to extract video metadata
    ffprobe_command = [
        'ffprobe',
        '-v', 'error',
        '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height',
        '-of', 'csv=s=x:p=0',
        local_video_path
    ]
    process = subprocess.Popen(ffprobe_command, stdout=subprocess.PIPE)
    output, _ = process.communicate()

    # Get the extracted width and height
    dimensions = output.decode().strip().split('x')
    extracted_width, extracted_height = dimensions[0], dimensions[1]

    # Use mimetypes to determine the MIME type
    mime_type, _ = mimetypes.guess_type(local_video_path)
    if not mime_type:
        mime_type = 'application/octet-stream'

    # Get the original object's metadata
    response = s3_client.head_object(Bucket=bucket_name, Key=object_key)
    original_metadata = response['Metadata']
    print(original_metadata)

    # Create the new metadata
    new_metadata = {
        'width': extracted_width,
        'height': extracted_height,
        'Content-Type': mime_type
    }

    # Copy the object and update metadata in S3
    s3_client.copy_object(
        Bucket=bucket_name,
        CopySource={'Bucket': bucket_name, 'Key': object_key},
        Key=object_key,
        Metadata=new_metadata,
        MetadataDirective='REPLACE'
    )

    s3_client.put_object_acl(
        ACL='public-read',
        Bucket=bucket_name,
        Key=object_key
    )

# Retrieve the object names
objects = s3_client.list_objects_v2(Bucket=bucket_name)

# List for the index
generated_urls=[]

for object in objects['Contents']:
    url=object['Key']
    full_url="https://" + bucket_name + ".s3." + s3_region + ".amazonaws.com/" + url

    # Update Metadata
    local_video_path = local_video_path + "/" + url
    update_video_metadata(s3_client, bucket_name, url, local_video_path)

    metadata = s3_client.head_object(Bucket=bucket_name, Key=url)
    parsed_url = urlparse(full_url)
    filename = parsed_url.path.split('/')[-1]
    file_extension = filename.split('.')[-1]
    filename_without_extension = '.'.join(filename.split('.')[:-1])

    # Check if the file extension is supported (mp4 or mov)
    if file_extension in supported_extensions:

        width = metadata['Metadata']['width']
        height = metadata['Metadata']['height']
        mime_type = metadata['Metadata']['content-type']
        video_dict = {}
        video_dict.update({'title': url})
        video_dict.update({'width': width})
        video_dict.update({'height': height})
        video_dict.update({'video_url': full_url})
        video_dict.update({'mime_type': mime_type})
        video_dict.update({'page_name': filename_without_extension})

        generate_video_pages(s3_client, bucket_name, video_dict, templates_folder)

        generated_urls.append(video_dict)

generate_index_page(s3_client, bucket_name, generated_urls, templates_folder)

# Static website settings
website_configuration = {
    'IndexDocument': {'Suffix': index_doc}
}

s3_client.put_bucket_website(
    Bucket=bucket_name,
    WebsiteConfiguration=website_configuration
)
