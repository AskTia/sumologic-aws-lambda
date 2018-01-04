import unittest
import boto3
import json
from time import sleep
import os


class TestLambda(unittest.TestCase):
    DLQ_QUEUE_NAME = 'SumoCWDeadLetterQueue'
    PROCESS_DLQ_LAMBDA = 'SumoCWProcessDLQLambda'
    TEMPLATE_KEYS_TO_REMOVE = ['SumoCWProcessDLQScheduleRule',
                               'SumoCWEventsInvokeLambdaPermission']

    def setUp(self):
        self.config = {
            'AWS_REGION_NAME': os.environ.get("AWS_DEFAULT_REGION",
                                              "us-east-1")
        }
        # aws_access_key_id aws_secret_access_key
        self.stack_name = "TestCWLStack"
        self.cf = boto3.client('cloudformation',
                               self.config['AWS_REGION_NAME'])
        self.template_name = 'DLQLambdaCloudFormation.json'
        self.template_data = self._parse_template(self.template_name)

    def tearDown(self):
        if self.stack_exists(self.stack_name):
            self.delete_stack()

    def test_lambda(self):
        upload_code_in_S3(self.config['AWS_REGION_NAME'])
        self.create_stack()
        print("Testing Stack Creation")
        self.assertTrue(self.stack_exists(self.stack_name))
        self.insert_mock_logs_in_DLQ()
        self.assertTrue(int(self._get_message_count()) == 50)
        self.invoke_lambda()
        self.check_consumed_messages_count()

    def stack_exists(self, stack_name):
        stacks = self.cf.list_stacks()['StackSummaries']
        for stack in stacks:
            if stack['StackStatus'] == 'DELETE_COMPLETE':
                continue
            if stack_name == stack['StackName'] and stack['StackStatus'] == 'CREATE_COMPLETE':
                print("%s stack exists" % stack_name)
                return True
        return False

    def create_stack(self):
        params = {
            'StackName': self.stack_name,
            'TemplateBody': self.template_data,
            'Capabilities': ['CAPABILITY_IAM']
        }
        stack_result = self.cf.create_stack(**params)
        print('Creating {}'.format(self.stack_name), stack_result)
        waiter = self.cf.get_waiter('stack_create_complete')
        print("...waiting for stack to be ready...")
        waiter.wait(StackName=self.stack_name)

    def delete_stack(self):
        params = {
            'StackName': self.stack_name
        }
        stack_result = self.cf.delete_stack(**params)
        print('Deleting {}'.format(self.stack_name), stack_result)
        waiter = self.cf.get_waiter('stack_delete_complete')
        print("...waiting for stack to be removed...")
        waiter.wait(StackName=self.stack_name)

    def _get_dlq_url(self):
        if (not hasattr(self, 'dlq_queue_url')):
            sqs = boto3.resource('sqs', self.config['AWS_REGION_NAME'])
            queue = sqs.get_queue_by_name(QueueName=self.DLQ_QUEUE_NAME)
            self.dlq_queue_url = queue.url

        return self.dlq_queue_url

    def insert_mock_logs_in_DLQ(self):
        print("Inserting fake logs in DLQ")
        dlq_queue_url = self._get_dlq_url()
        sqs_client = boto3.client('sqs', self.config['AWS_REGION_NAME'])
        mock_logs = json.load(open('cwlfixtures.json'))
        for log in mock_logs:
            sqs_client.send_message(QueueUrl=dlq_queue_url,
                                    MessageBody=json.dumps(log))

        self.initial_log_count = self._get_message_count()
        print("Inserted %s Messages in %s" % (
            self.initial_log_count, dlq_queue_url))

    def _get_message_count(self):
        sqs = boto3.resource('sqs', self.config['AWS_REGION_NAME'])
        queue = sqs.get_queue_by_name(QueueName=self.DLQ_QUEUE_NAME)
        return int(queue.attributes.get('ApproximateNumberOfMessages'))

    def _get_dlq_function_name(self, lambda_client, pattern):
        import re
        for func in lambda_client.list_functions()['Functions']:
            if re.search(pattern, func['FunctionName']):
                return func['FunctionName']
        return ''

    def invoke_lambda(self):
        lambda_client = boto3.client('lambda', self.config['AWS_REGION_NAME'])
        lambda_func_name = self._get_dlq_function_name(lambda_client,
                                                       self.PROCESS_DLQ_LAMBDA)
        response = lambda_client.invoke(FunctionName=lambda_func_name)
        print("Invoking lambda function", response)

    def check_consumed_messages_count(self):
        sleep(120)
        final_message_count = self._get_message_count()
        print("Testing number of consumed messages initial: %s final: %s processed: %s" % (
            self.initial_log_count, final_message_count,
            self.initial_log_count - final_message_count))
        self.assertGreater(self.initial_log_count, final_message_count)

    def _parse_template(self, template):
        with open(template) as template_fileobj:
            template_data = template_fileobj.read()
        print("Validating cloudformation template")
        self.cf.validate_template(TemplateBody=template_data)
        #removing schedulerule to prevent lambda being triggered while testing
        #becoz we are invoking lambda directly
        template_data = eval(template_data)
        for key in self.TEMPLATE_KEYS_TO_REMOVE:
            template_data["Resources"].pop(key)
        template_data = str(template_data)

        return template_data


def upload_code_in_multiple_regions():
    regions = [
        "us-east-2",
        "us-east-1",
        "us-west-1",
        "us-west-2",
        "ap-south-1",
        "ap-northeast-2",
        "ap-southeast-1",
        "ap-southeast-2",
        "ap-northeast-1",
        "ca-central-1",
    # "cn-north-1",
        "eu-central-1",
        "eu-west-1",
        "eu-west-2",
        "eu-west-3",
        "sa-east-1"
    ]

    # for region in regions:
    #     create_bucket(region)

    for region in regions:
        upload_code_in_S3(region)


def get_bucket_name(region):
    return '%s-%s' % ("appdevstore", region)


def create_bucket(region):
    s3 = boto3.client('s3', region)
    bucket_name = get_bucket_name(region)
    if region == "us-east-1":
        response = s3.create_bucket(Bucket=bucket_name)
    else:
        response = s3.create_bucket(Bucket=bucket_name, CreateBucketConfiguration={
            'LocationConstraint': region
        })
    print("Creating bucket", region, response)


def upload_code_in_S3(region):
    print("Uploading zip file in S3")
    s3 = boto3.client('s3', region)
    filename = 'dlqprocessor.zip'
    bucket_name = get_bucket_name(region)
    s3.upload_file(filename, bucket_name, filename)


def generate_fixtures(region, count):
    data = []
    sqs = boto3.client('sqs', region)
    for x in range(0, count, 10):
        response = sqs.receive_message(
            QueueUrl='https://sqs.us-east-2.amazonaws.com/456227676011/SumoCWDeadLetterQueue',
            MaxNumberOfMessages=10,
        )
        for msg in response['Messages']:
            data.append(eval(msg['Body']))

    return data[:count]


if __name__ == '__main__':
    unittest.main()
    # upload_code_in_multiple_regions()
